"""The command registry: specs -> actions, layout -> ribbon, state -> enabled.

Uses a stub window so the machinery is tested without `MainWindow`.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from raptor.gui.commands import (AppState, Btn, CommandRegistry,
                                 CommandSpec, Custom, Group, Page,
                                 PREDICATES, UNMET, build_pages)
from raptor.gui.widgets.ribbon import Ribbon, RibbonButton

pytestmark = pytest.mark.gui


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


class StubWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple] = []
        self._ribbon = Ribbon()

        class Sub:
            def __init__(self, outer):
                self.outer = outer

            def act(self, *a, **k):
                self.outer.calls.append(("sub.act", a, k))
        self.sub = Sub(self)
        self.built: list[str] = []

    def do_it(self, *a, **k):
        self.calls.append(("do_it", a, k))

    def build_extra(self, group):
        self.built.append(group._label.text())


def _reg(win, *specs, **kw):
    reg = CommandRegistry(win, specs, **kw)
    reg.create_all()
    return reg


def test_builds_an_action_and_stores_it_under_its_attr(qapp):
    w = StubWindow()
    reg = _reg(w, CommandSpec(
        id="t.open", attr="_open_action", text="Open", tooltip="Open it",
        shortcut="Ctrl+O", checkable=True, checked=True))
    a = w._open_action
    assert a is reg.action("t.open")
    assert (a.text(), a.toolTip()) == ("Open", "Open it")
    assert a.shortcut().toString() == "Ctrl+O"
    assert a.isCheckable() and a.isChecked()


def test_handler_receives_args_and_kwargs(qapp):
    w = StubWindow()
    _reg(w, CommandSpec(id="t.a", attr="_a", text="A", handler="do_it",
                        args=(1, 2), kwargs=(("quiet", False),)))
    w._a.trigger()
    assert w.calls == [("do_it", (1, 2), {"quiet": False})]


def test_dotted_handler_path_is_resolved(qapp):
    w = StubWindow()
    _reg(w, CommandSpec(id="t.a", attr="_a", text="A", handler="sub.act"))
    w._a.trigger()
    assert w.calls[0][0] == "sub.act"


def test_handler_is_looked_up_when_fired_not_when_built(qapp):
    """A handler replaced after construction is still the one called."""
    w = StubWindow()
    _reg(w, CommandSpec(id="t.a", attr="_a", text="A", handler="do_it"))
    seen = []
    w.do_it = lambda *a, **k: seen.append("patched")
    w._a.trigger()
    assert seen == ["patched"] and w.calls == []


def test_toggled_commands_get_the_checked_state_last(qapp):
    w = StubWindow()
    _reg(w, CommandSpec(id="t.tool", attr="_tool", text="Tool",
                        checkable=True, on="toggled", handler="do_it",
                        args=("add_block",)))
    w._tool.setChecked(True)
    w._tool.setChecked(False)
    assert w.calls == [("do_it", ("add_block", True), {}),
                       ("do_it", ("add_block", False), {})]


def test_extra_shortcuts_and_standard_keys(qapp):
    w = StubWindow()
    _reg(w, CommandSpec(id="t.redo", attr="_redo", text="Redo",
                        standard_key="Redo", more_shortcuts=("Ctrl+Shift+Z",)))
    keys = [k.toString() for k in w._redo.shortcuts()]
    assert "Ctrl+Shift+Z" in keys and len(keys) >= 2


def test_application_shortcut_context(qapp):
    w = StubWindow()
    _reg(w, CommandSpec(id="t.s", attr="_s", text="S", shortcut="Ctrl+S",
                        context="application"))
    assert w._s.shortcutContext() == Qt.ShortcutContext.ApplicationShortcut


def test_unmanaged_commands_keep_their_initial_enabled_state(qapp):
    w = StubWindow()
    _reg(w,
         CommandSpec(id="t.on", attr="_on", text="On"),
         CommandSpec(id="t.off", attr="_off", text="Off", enabled=False))
    assert w._on.isEnabled() and not w._off.isEnabled()


class TestEnablement:
    def _window(self):
        w = StubWindow()
        reg = _reg(w,
                   CommandSpec(id="t.p", attr="_p", text="Print",
                               tooltip="Print it",
                               enabled_when="model_open"),
                   CommandSpec(id="t.s", attr="_s", text="Save",
                               tooltip="Save (Ctrl+S)",
                               enabled_when="has_file"))
        return w, reg

    def test_managed_commands_start_unavailable(self, qapp):
        w, _ = self._window()
        assert not w._p.isEnabled() and not w._s.isEnabled()

    def test_refresh_applies_the_state(self, qapp):
        w, reg = self._window()
        reg.refresh(AppState(model_open=True, has_file=False))
        assert w._p.isEnabled() and not w._s.isEnabled()
        reg.refresh(AppState(model_open=True, has_file=True))
        assert w._p.isEnabled() and w._s.isEnabled()
        reg.refresh(AppState())
        assert not w._p.isEnabled() and not w._s.isEnabled()

    def test_a_disabled_command_says_why_in_its_tooltip(self, qapp):
        w, reg = self._window()
        reg.refresh(AppState())
        tip = w._p.toolTip()
        assert tip.startswith("Print it")
        assert "Unavailable" in tip and UNMET["model_open"] in tip

    def test_the_reason_goes_away_when_available(self, qapp):
        w, reg = self._window()
        reg.refresh(AppState())
        reg.refresh(AppState(model_open=True))
        assert w._p.toolTip() == "Print it"

    def test_refreshing_twice_does_not_stack_reasons(self, qapp):
        w, reg = self._window()
        for _ in range(3):
            reg.refresh(AppState())
        assert w._p.toolTip().count("Unavailable") == 1

    def test_a_tooltip_rewritten_elsewhere_survives(self, qapp):
        """Save's tooltip is rewritten per open file. Only the suffix this
        registry added may be touched."""
        w, reg = self._window()
        reg.refresh(AppState(model_open=True))
        w._s.setToolTip("Save to model.rbds (Ctrl+S)")
        reg.refresh(AppState(model_open=True, has_file=True))
        assert w._s.toolTip() == "Save to model.rbds (Ctrl+S)"
        reg.refresh(AppState())
        assert w._s.toolTip().startswith("Save to model.rbds (Ctrl+S)")
        reg.refresh(AppState(model_open=True, has_file=True))
        assert w._s.toolTip() == "Save to model.rbds (Ctrl+S)"

    def test_a_spec_can_override_the_reason(self, qapp):
        w = StubWindow()
        reg = _reg(w, CommandSpec(id="t.p", attr="_p", text="P",
                                  tooltip="Do P",
                                  enabled_when="model_open",
                                  unmet="select two blocks"))
        reg.refresh(AppState())
        assert w._p.toolTip() == "Do P\n\nUnavailable: select two blocks"
        assert UNMET["model_open"] not in w._p.toolTip()

    def test_tracking_a_widget_built_elsewhere(self, qapp):
        from PySide6.QtWidgets import QPushButton
        _w, reg = self._window()
        btn = QPushButton("Rates")
        btn.setToolTip("Rates library")
        reg.track(btn, "model_open")
        reg.refresh(AppState())
        assert not btn.isEnabled() and "Unavailable" in btn.toolTip()
        reg.refresh(AppState(model_open=True))
        assert btn.isEnabled() and btn.toolTip() == "Rates library"

    def test_unknown_predicate_is_rejected_at_build_time(self, qapp):
        w = StubWindow()
        with pytest.raises(ValueError, match="unknown predicate"):
            _reg(w, CommandSpec(id="t.x", attr="_x", text="X",
                                enabled_when="nonsense"))

    def test_every_predicate_has_a_reason(self):
        assert set(PREDICATES) <= set(UNMET)


def test_duplicate_ids_are_rejected(qapp):
    w = StubWindow()
    with pytest.raises(ValueError, match="duplicate"):
        CommandRegistry(w, [CommandSpec(id="x", attr="_a", text="A"),
                            CommandSpec(id="x", attr="_b", text="B")])


class TestBuildPages:
    def _layout(self):
        return (
            Page("First", "_first_index", (
                Group("Alpha", (Btn("t.a", large=True), Btn("t.b"))),
                Group("Beta", (Custom("build_extra"),)),
            )),
            Page("Second", "_second_index", (
                Group("Gamma", (Btn("t.b"),)),      # same command twice
            )),
        )

    def _setup(self):
        w = StubWindow()
        reg = _reg(w,
                   CommandSpec(id="t.a", attr="_a", text="A"),
                   CommandSpec(id="t.b", attr="_b", text="B"))
        build_pages(w, reg, self._layout())
        return w

    def test_pages_are_added_in_order_with_their_indices(self, qapp):
        w = self._setup()
        assert (w._first_index, w._second_index) == (0, 1)
        r = w._ribbon
        assert [r.tab_bar.tabText(i) for i in range(2)] == ["First",
                                                            "Second"]

    def test_groups_and_buttons_land_where_the_layout_says(self, qapp):
        w = self._setup()
        page = w._ribbon._pages[0]
        assert [g._label.text() for g in page._groups] == ["Alpha", "Beta"]
        btns = [b for b in page._groups[0].findChildren(RibbonButton)]
        assert [(b.text(), bool(b.property("ribbonLarge")))
                for b in btns] == [("A", True), ("B", False)]

    def test_a_custom_builder_is_called_with_its_group(self, qapp):
        w = self._setup()
        assert w.built == ["Beta"]

    def test_one_command_can_appear_on_two_pages(self, qapp):
        w = self._setup()
        second = w._ribbon._pages[1]._groups[0]
        assert [b.text() for b in second.findChildren(RibbonButton)] == ["B"]


def test_refresh_can_be_limited_to_one_predicate(qapp):
    """After Save As only "has_file" changed. A full refresh would also
    re-enable commands a run in progress has switched off."""
    w = StubWindow()
    reg = _reg(w,
               CommandSpec(id="t.p", attr="_p", text="P",
                           enabled_when="model_open"),
               CommandSpec(id="t.s", attr="_s", text="S",
                           enabled_when="has_file"))
    reg.refresh(AppState(model_open=True, has_file=False))
    w._p.setEnabled(False)          # something else (a run) turned it off
    reg.refresh(AppState(model_open=True, has_file=True),
                only={"has_file"})
    assert w._s.isEnabled()
    assert not w._p.isEnabled(), "a limited refresh must leave it alone"
