"""Inspector block tabs: general, failure, repair, spares, maintenance, dependency and costs.

Expects on `self`: the Inspector's tab layouts and the selected block."""
from __future__ import annotations

from raptor.gui.ui.inspector_common import (
    _SIM_START,
    _WordWrapCheckBox,
)

from raptor.gui.ui.qtcompat import req

from typing import TYPE_CHECKING, Any
from PySide6.QtWidgets import (
    QWidget, QCheckBox, QDoubleSpinBox, QComboBox, QPushButton,
    QRadioButton, QButtonGroup,
)

from raptor.core.model import RBDBlock, SparingType
from raptor.gui.resources.design_tokens import (
    active_colour,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _Base
    import PySide6.QtCore
    import PySide6.QtWidgets
else:
    _Base = object


class InspectorBlockMixin(_Base):
    """Inspector block tabs: general, failure, repair, spares, maintenance, dependency and costs."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        # `x: Any  # really T | None` marks an attribute the host sets to None first and
        # fills in later: declaring it as `T` would be wrong and `T | None` would force
        # a None check at every use, so it stays Any with its real type noted.
        def _clear_tab(self, scroll: Any) -> Any: ...
        _comment_edit: Any
        def _comment_editor(self, etype: str, oid: int, name: str, on_commit: Any) -> Any: ...
        def _comment_markdown_hint(self, form: Any, edit: Any) -> Any: ...
        _comment_setter: Any
        def _configure_tabs(self, kind: str) -> Any: ...
        def _convert_comment_button(self, form: Any, etype: str, oid: int, edit: Any) -> Any: ...
        _current_block_id: int | None
        _current_node_id: int | None
        _deferred_tabs: dict[int, object]
        def _dependency_group(self, layout: Any, oid: int, dependency_num: int, dep_type: str, emit: Any = ...) -> Any: ...
        def _distribution_params_row(self, form: Any, distro: int, use: str, values: list[float], on_commit: Any, addr_prefix: str | None = ..., lambda_mode: bool | None = ...) -> Any: ...
        def _distro_type_row(self, form: Any, distro: int, is_failure: bool, bid: int, signal: Any) -> None: ...
        def _float_row(self, form: Any, label: str, value: float, on_commit: Any, is_time: bool = ...) -> Any: ...
        def _group(self, title: str, layout: Any) -> Any: ...
        _header: PySide6.QtWidgets.QLabel
        def _name_edit(self, form: Any, current_name: str, on_commit: Any) -> Any: ...
        def _phasing_group(self, layout: Any, kind: str, oid: int, phased_now: bool, entries_now: list[tuple[str, float]]) -> Any: ...
        _populating_comment: bool
        _position_labels: Any  # None until set; really tuple | None
        _position_source: Any  # None until set; really RBDNode | None
        _rbd: Any  # really raptor.core.fileio.rbd.RbdFile | None
        def _restore_tab(self) -> Any: ...
        @staticmethod
        def _row(form: Any, label: str, value: Any, tooltip: str = ...) -> Any: ...
        def _run_deferred(self, index: int) -> None: ...
        _scroll_costs: PySide6.QtWidgets.QScrollArea
        _scroll_deps: PySide6.QtWidgets.QScrollArea
        _scroll_failure: PySide6.QtWidgets.QScrollArea
        _scroll_general: PySide6.QtWidgets.QScrollArea
        _scroll_pm: PySide6.QtWidgets.QScrollArea
        _scroll_repair: PySide6.QtWidgets.QScrollArea
        _scroll_spares: PySide6.QtWidgets.QScrollArea
        def _stream_editor_row(self, form: Any, cur: int, which: str, bid: int) -> None: ...
        _tabs: PySide6.QtWidgets.QTabWidget
        block_always_done_changed: PySide6.QtCore.SignalInstance
        block_comment_changed: PySide6.QtCore.SignalInstance
        block_costs_changed: PySide6.QtCore.SignalInstance
        block_dep_changed: PySide6.QtCore.SignalInstance
        block_dep_default_idle_changed: PySide6.QtCore.SignalInstance
        block_dep_master_changed: PySide6.QtCore.SignalInstance
        block_duty_cycle_changed: PySide6.QtCore.SignalInstance
        block_fail_params_changed: PySide6.QtCore.SignalInstance
        block_fail_type_changed: PySide6.QtCore.SignalInstance
        block_gd_enabled_changed: PySide6.QtCore.SignalInstance
        block_gd_rate_limit_changed: PySide6.QtCore.SignalInstance
        block_gd_type_changed: PySide6.QtCore.SignalInstance
        block_init_stock_changed: PySide6.QtCore.SignalInstance
        block_new_spares_changed: PySide6.QtCore.SignalInstance
        block_pm_dist_changed: PySide6.QtCore.SignalInstance
        block_pm_enabled_changed: PySide6.QtCore.SignalInstance
        block_pm_freq_changed: PySide6.QtCore.SignalInstance
        block_pm_option_changed: PySide6.QtCore.SignalInstance
        block_pm_params_changed: PySide6.QtCore.SignalInstance
        block_pm_stagger_changed: PySide6.QtCore.SignalInstance
        block_pm_trig_changed: PySide6.QtCore.SignalInstance
        block_post_ldt_dist_changed: PySide6.QtCore.SignalInstance
        block_post_ldt_params_changed: PySide6.QtCore.SignalInstance
        block_pre_ldt_dist_changed: PySide6.QtCore.SignalInstance
        block_pre_ldt_params_changed: PySide6.QtCore.SignalInstance
        block_renamed: PySide6.QtCore.SignalInstance
        block_repair_params_changed: PySide6.QtCore.SignalInstance
        block_repair_type_changed: PySide6.QtCore.SignalInstance
        block_resource_counts_changed: PySide6.QtCore.SignalInstance
        block_resource_pool_changed: PySide6.QtCore.SignalInstance
        block_restoration_factor_changed: PySide6.QtCore.SignalInstance
        block_restoration_type_changed: PySide6.QtCore.SignalInstance
        block_standby_stress_changed: PySide6.QtCore.SignalInstance
        block_start_age_changed: PySide6.QtCore.SignalInstance
        def ensure_all_tabs_built(self) -> None: ...
        lazy_tabs: bool
        manage_pool_requested: PySide6.QtCore.SignalInstance
        pool_name_changed: PySide6.QtCore.SignalInstance
        sparing_type_changed: PySide6.QtCore.SignalInstance
        validation_failed: PySide6.QtCore.SignalInstance

    def _populate_block(self, block: RBDBlock):
        self._populating_comment = True
        try:
            self._populate_block_inner(block)
        finally:
            self._populating_comment = False

    def _populate_block_inner(self, block: RBDBlock):
        self._configure_tabs("block")
        self._header.setText(f"Block: {block.name}  (ID {block.id})")
        self._current_block_id = block.id
        self._current_node_id = None
        # General (with the Position labels) may not be built now: until it
        # is, there are no labels for this block to keep up to date.
        self._position_source = None
        self._position_labels = None
        # Only the tab that will be shown is built now; the rest are built when the user opens them. Building all
        # seven on every canvas click was most of the lag when selecting elements.
        blocks = getattr(self, "_blocks_source", None) or {}
        bid = block.id

        def live() -> RBDBlock:
            return blocks.get(bid, block)      # the view's current object, not the one captured at click time

        def general() -> None:
            b = live()
            self._populating_comment = True
            try:
                self._populate_general(b)
            finally:
                self._populating_comment = False
            w = self._scroll_general.widget()
            self._phasing_group(
                req(w).layout(), "block", b.id, b.uses_phasing, list(zip(b.phase_type, b.phase_value)))

        steps = [(self._scroll_general, general),
                 (self._scroll_failure, lambda: self._populate_failure(live())),
                 (self._scroll_repair, lambda: self._populate_repair(live())),
                 (self._scroll_spares, lambda: self._populate_spares(live())),
                 (self._scroll_pm, lambda: self._populate_pm(live())),
                 (self._scroll_deps, lambda: self._populate_deps(live())),
                 (self._scroll_costs, lambda: self._populate_costs(live()))]
        self._deferred_tabs = {self._tabs.indexOf(scroll): build for scroll, build in steps}
        self._restore_tab()
        if self.lazy_tabs:
            self._run_deferred(self._tabs.currentIndex())
        else:
            self.ensure_all_tabs_built()

    def _populate_general(self, block: RBDBlock):
        w = self._scroll_general.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_general)
        _c = active_colour()

        _gb, fl = self._group("Identity", layout)
        block_id = block.id
        self._name_edit(fl, block.name, lambda new_name: self.block_renamed.emit(block_id, new_name))
        self._row(fl, "ID", block.id)
        self._row(fl, "Parent ID", block.parent_id)
        # Legacy Raptor 7 comment (Part #, data provenance…) — editable,
        # multi-line; '~' separators from the file become line breaks.
        # Debounced like every other kind's `_comment_row`: per-key
        # commits rebuilt the Inspector and reset the caret/scroll.
        self._comment_setter = lambda txt: self.block_comment_changed.emit(
            block_id, txt)
        self._comment_edit = self._comment_editor(
            "block", block.id, block.name,
            lambda txt: self._comment_setter(txt))
        fl.addRow("Comment:", self._comment_edit)
        self._comment_markdown_hint(fl, self._comment_edit)
        self._convert_comment_button(fl, "block", block.id,
                                     self._comment_edit)

        _gb, fl = self._group("Position", layout)
        self._position_source = block
        self._position_labels = (
            self._row(fl, "X", f"{block.x_position:.3f}"),
            self._row(fl, "Y", f"{block.y_position:.3f}"))

        _gb, fl = self._group("Connections", layout)
        self._row(fl, "Inputs", block.connect_into_num)
        self._row(fl, "Outputs", block.connect_out_of_num)

        _gb, fl = self._group("Simulation", layout)
        start = _SIM_START.get(block.sim_start_type, f"Unknown ({block.sim_start_type})")
        self._row(fl, "Start state", start,
                  "How the block begins each run: working or failed, and "
                  "how much of its life or repair is already done.")
        self._row(fl, "Uses phasing", "Yes" if block.uses_phasing else "No")
        # For "% repair complete" starts the value is a percentage of the
        # repair cycle; for "some life exhausted" starts it's time units.
        if block.sim_start_type == 5:
            self._row(fl, "% complete", f"{block.amount_exhausted:.3f}")
        elif block.sim_start_type in (3,):
            self._row(fl, "Amount exhausted", f"{block.amount_exhausted:.3f}",
                      "Life already used at time 0. It "
                      "adds to Start age.")
        if block.sim_start_type == 3:
            age_edit = self._float_row(
                fl, "Start age", block.start_age,
                lambda v, bid=block_id:
                    self.block_start_age_changed.emit(bid, v),
                is_time=True)
            age_edit.setToolTip(
                "How old the block already is at time 0, for a block "
                "partway through its life. Its first life is drawn given it "
                "has survived to this age, so a worn block fails sooner. It "
                "also counts as age already used when a restoration factor "
                "applies. 0 = new.")

    def _populate_failure(self, block: RBDBlock):
        w = self._scroll_failure.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_failure)

        gb, fl = self._group("Failure Distribution", layout)
        self._distro_type_row(fl, block.fail_distro, True, block.id,
                              self.block_fail_type_changed)
        self._stream_editor_row(fl, block.fail_stream, "F", block.id)
        block_id = block.id
        self._distribution_params_row(
            fl, block.fail_distro, "Failure", block.fail_values,
            lambda vals: self.block_fail_params_changed.emit(block_id, vals),
            addr_prefix=f"block:{block_id}:F",
        )

        # Advanced tab fields (Raptor 7 "Mean Life Growth and
        # Decay" plus Raptor 7 "Standby Stress") folded in here: Raptor 7's
        # Block Properties dialog has a separate Advanced tab,
        # but this Inspector is a fixed 7-slot layout with every slot
        # spoken for (same precedent as Simulation Theory being folded
        # into the Failure/Repair stream editors). Growth/decay can be
        # switched on from scratch here.
        gb, fl = self._group("Advanced", layout)
        gb.setToolTip(
            "How this block wears: ageing in standby, how much of the time "
            "it works, what a repair or PM does to its age, and whether its "
            "lives get longer or shorter after each failure.")
        stress_spin = QDoubleSpinBox()
        stress_spin.setRange(0.0, 100.0)
        stress_spin.setDecimals(4)
        stress_spin.setValue(float(block.sb_stress))
        stress_spin.setAccessibleName("Standby stress")
        stress_spin.setToolTip(
            "How fast the block ages while it waits in standby, as a "
            "multiple of its normal rate. 0 = cold standby (no ageing while "
            "waiting), between 0 and 1 = warm, 1 or more = hot (ages at "
            "the normal rate, or faster, even while waiting).")
        stress_spin.editingFinished.connect(
            lambda bid=block_id, sp=stress_spin:
                self.block_standby_stress_changed.emit(bid, sp.value()))
        fl.addRow("Standby stress:", stress_spin)

        duty_spin = QDoubleSpinBox()
        duty_spin.setRange(0.000001, 1e9)
        duty_spin.setDecimals(6)
        duty_spin.setValue(float(block.duty_cycle))
        duty_spin.setAccessibleName("Duty cycle")
        duty_spin.setToolTip(
            "How hard the block works, as a share of its rated duty; it "
            "sets how fast the block uses up its life in calendar time. "
            "1 = working all the time (the default). 0.3 = working 30% of "
            "the time, so each life lasts 1/0.3 = 3.3 times as long. 1.5 = "
            "run 50% harder than rated. It multiplies any phase stress, "
            "and the standby stress while the block is in standby.")
        duty_spin.setSingleStep(0.05)
        duty_spin.setKeyboardTracking(False)
        duty_spin.valueChanged.connect(
            lambda v, bid=block_id:
                self.block_duty_cycle_changed.emit(bid, v))
        fl.addRow("Duty cycle:", duty_spin)

        restore_combo = QComboBox()
        restore_combo.addItem("Off — repair as good as new", 0)
        restore_combo.addItem("Type I — restore recent wear", 1)
        restore_combo.addItem("Type II — restore total age", 2)
        restore_combo.setCurrentIndex(
            max(0, restore_combo.findData(int(block.restoration_type))))
        restore_combo.setAccessibleName("Restoration type")
        restore_combo.setToolTip(
            "What a repair, or a PM with PM refresh off, does to the "
            "block's age.\n"
            "Off: Raptor's own behaviour. Every repair is as good as new; "
            "a PM renews the life or resumes it according to PM refresh.\n"
            "Type I (Kijima I): removes the restoration factor's share of "
            "the wear added since the previous repair or PM.\n"
            "Type II (Kijima II): removes that share of the block's whole "
            "age.\n"
            "With PM refresh ticked, a PM is always as good as new.")
        fl.addRow("Restoration:", restore_combo)

        restore_spin = QDoubleSpinBox()
        restore_spin.setRange(0.0, 1.0)
        restore_spin.setDecimals(6)
        restore_spin.setValue(float(block.restoration_factor))
        restore_spin.setAccessibleName("Restoration factor")
        restore_spin.setToolTip(
            "Share of the block's age a repair or PM removes. 0 = none: the "
            "block is as worn as just before it failed (as bad as old). "
            "1 = all: as good as new. 0.7 removes 70% and leaves 30%. "
            "Used only when Restoration is Type I or Type II.")
        restore_spin.setSingleStep(0.05)
        restore_spin.setKeyboardTracking(False)
        restore_spin.setEnabled(block.restoration_type in (1, 2))
        restore_spin.valueChanged.connect(
            lambda v, bid=block_id:
                self.block_restoration_factor_changed.emit(bid, v))
        def _restore_type_chosen(_i: int, bid: int = block_id,
                                 cmb: QComboBox = restore_combo,
                                 sp: QDoubleSpinBox = restore_spin) -> None:
            chosen = int(cmb.currentData())
            sp.setEnabled(chosen in (1, 2))
            self.block_restoration_type_changed.emit(bid, chosen)
        restore_combo.currentIndexChanged.connect(_restore_type_chosen)
        fl.addRow("Restoration factor:", restore_spin)

        gd_cb = QCheckBox(
            "This block's mean life changes following a failure")
        gd_cb.setToolTip(
            "Reliability growth or decay: after each failure the block's "
            "lives get longer (growth, e.g. design fixes) or shorter "
            "(decay, e.g. wear that repairs do not remove).")
        gd_cb.setChecked(block.gd_type != 0)
        gd_cb.toggled.connect(
            lambda on, bid=block_id:
                self.block_gd_enabled_changed.emit(bid, on))
        fl.addRow(gd_cb)
        if block.gd_type != 0:
            gd_combo = QComboBox()
            gd_combo.addItem("Linear", 1)
            gd_combo.addItem("Geometric", 2)
            gd_combo.addItem("Asymptotic", 3)
            gci = gd_combo.findData(block.gd_type
                                    if block.gd_type in (1, 2) else 3)
            gd_combo.setCurrentIndex(max(gci, 0))
            gd_combo.currentIndexChanged.connect(
                lambda _i, bid=block_id, cmb=gd_combo:
                    self.block_gd_type_changed.emit(bid, cmb.currentData()))
            gd_combo.setToolTip(
                "How the life changes with the number of failures n so "
                "far.\nLinear: life × (1 + rate × n).\nGeometric: life × "
                "rate^n.\nAsymptotic: the mean life moves from its "
                "starting value towards Limit, faster for a larger rate.")
            fl.addRow("Type:", gd_combo)

            rate_spin = QDoubleSpinBox()
            rate_spin.setRange(-1e9, 1e9)
            rate_spin.setDecimals(6)
            rate_spin.setValue(float(block.gd_rate))
            limit_spin = QDoubleSpinBox()
            limit_spin.setRange(-1e9, 1e9)
            limit_spin.setDecimals(6)
            limit_spin.setValue(float(block.gd_limit))

            def _commit_gd(bid=block_id, rs=rate_spin, ls=limit_spin):
                self.block_gd_rate_limit_changed.emit(
                    bid, [rs.value(), ls.value()])
            rate_spin.editingFinished.connect(_commit_gd)
            limit_spin.editingFinished.connect(_commit_gd)
            rate_spin.setToolTip(
                "How much the life changes per failure (see Type). "
                "Linear: above 0 is growth, below 0 decay. Geometric: "
                "above 1 is growth, below 1 decay. Asymptotic: how quickly "
                "the mean life approaches Limit.")
            limit_spin.setToolTip(
                "The mean life (in the model's time units) the change "
                "stops at: growth never goes above it and decay never "
                "below it.")
            fl.addRow("Rate:", rate_spin)
            fl.addRow("Limit:", limit_spin)

    def _populate_repair(self, block: RBDBlock):
        w = self._scroll_repair.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_repair)

        _gb, fl = self._group("Repair Distribution", layout)
        if block.repair_distro == 0:
            # A repair distro of 0 means "no repair law" -- same meaning as
            # code 18 for a repair, so both read the same way. The label
            # comes from `distro_combo` (review E-14); this used to be a
            # fourth, independent copy of the code-18 rule.
            from raptor.gui.ui.distro_combo import _UNREPAIRABLE
            self._row(fl, "Type", _UNREPAIRABLE)
        else:
            self._distro_type_row(fl, block.repair_distro, False,
                                  block.id, self.block_repair_type_changed)
            if block.repair_distro != 18:
                self._stream_editor_row(fl, block.repair_stream, "R",
                                        block.id)
            block_id = block.id
            self._distribution_params_row(
                fl, block.repair_distro, "Repair", block.repair_values,
                lambda vals: self.block_repair_params_changed.emit(block_id, vals),
                addr_prefix=f"block:{block_id}:R",
            )

    def _populate_spares(self, block: RBDBlock):
        w = self._scroll_spares.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_spares)

        st = block.sparing_type
        gb, fl = self._group("Sparing", layout)
        bid2 = block.id
        type_combo = QComboBox()
        # Words a user reads, not the enum's names (NONE, SPARE_POOL).
        for label, type_name in (("None — repair in place", "NONE"),
                             ("Spare pool", "SPARE_POOL"),
                             ("Infinite spares", "INFINITE"),
                             ("Custom stock", "CUSTOM")):
            type_combo.addItem(label, type_name)
        ti = type_combo.findData(st.name)
        type_combo.setCurrentIndex(max(ti, 0))
        type_combo.setToolTip(
            "Replacement strategy: none (repair in place), a spare pool "
            "(draw from a pool), infinite (a spare is always available), "
            "custom (this block's own stock and arrival schedule)")
        type_combo.activated.connect(
            lambda _i, b=bid2, cmb=type_combo:
            self.sparing_type_changed.emit(b, cmb.currentData()))
        fl.addRow("Type:", type_combo)

        pool_combo = QComboBox()
        pool_combo.addItem("(none)", "")
        pools_sec = (self._rbd.sections.get("SPARE_POOL_DATA")
                     if self._rbd else None)
        for r3 in (pools_sec.rows if pools_sec else []):
            param_name = r3.get("PoolName", "")
            if param_name:
                pool_combo.addItem(param_name, param_name)
        pi = pool_combo.findText(block.pool_name or "")
        pool_combo.setCurrentIndex(max(pi, 0))
        pool_combo.setEnabled(st in (SparingType.SPARE_POOL,))
        pool_combo.setToolTip("Spare pool this block draws from")
        pool_combo.activated.connect(
            lambda _i, b=bid2, cmb=pool_combo:
            self.pool_name_changed.emit(b, cmb.currentText()))
        fl.addRow("Pool:", pool_combo)
        manage_pools_btn = QPushButton("Manage Spare Pools…")
        manage_pools_btn.setToolTip(
            "Create, edit or delete spare pools")
        manage_pools_btn.clicked.connect(
            lambda: self.manage_pool_requested.emit("SPARE_POOL_DATA"))
        fl.addRow(manage_pools_btn)

        if st in (SparingType.SPARE_POOL, SparingType.INFINITE, SparingType.CUSTOM):
            gb, fl = self._group("Spare Stock", layout)
            if st == SparingType.CUSTOM:
                # Engine reads block.init_stock only for Custom sparing
                # (block_lifecycle's spares_remaining; cost `sic`) --
                # editing it on a Pooled block would be a silent no-op.
                from PySide6.QtWidgets import QSpinBox
                spin = QSpinBox()
                spin.setRange(0, 999_999)
                spin.setValue(int(block.init_stock))
                last = {"v": int(block.init_stock)}

                def commit_init():
                    if spin.value() != last["v"]:
                        last["v"] = spin.value()
                        self.block_init_stock_changed.emit(block.id,
                                                           spin.value())
                spin.valueChanged.connect(commit_init)
                fl.addRow("Initial stock:", spin)
            else:
                hint = " (pool-managed)" if st == SparingType.SPARE_POOL \
                    else ""
                self._row(fl, "Initial stock", f"{block.init_stock}{hint}")
            if st == SparingType.CUSTOM:
                # Same CUSTOM-gating as Initial stock above -- the engine
                # reads new_spares/new_spares_arrival for its per-block
                # arrival queue only under Custom sparing (`pool.py`'s
                # block-scoped spare-cycle generator); editing
                # these for Pooled/Infinite blocks would be a no-op.
                from PySide6.QtWidgets import QSpinBox
                count_spin = QSpinBox()
                count_spin.setRange(0, 999_999)
                count_spin.setValue(int(block.new_spares))
                interval_spin = QDoubleSpinBox()
                interval_spin.setRange(0.0, 1e9)
                interval_spin.setDecimals(3)
                interval_spin.setValue(float(block.new_spares_arrival))

                def _commit_new_spares(bid=block.id, cs=count_spin,
                                       ivs=interval_spin):
                    self.block_new_spares_changed.emit(
                        bid, [cs.value(), ivs.value()])
                count_spin.editingFinished.connect(_commit_new_spares)
                interval_spin.editingFinished.connect(_commit_new_spares)
                fl.addRow("New spares/order:", count_spin)
                fl.addRow("Arrival interval:", interval_spin)
            else:
                self._row(fl, "New spares/order", block.new_spares)
                self._row(fl, "Arrival interval",
                          f"{block.new_spares_arrival:.3f}")

        if st == SparingType.SPARE_POOL:
            gb, fl = self._group("Spare Ordering", layout)
            self._row(fl, "Routine (RSO)",
                      "Yes" if block.routine_spare_ordering else "No",
                      "Routine Spare Ordering (RSO): reorder a spare on "
                      "a fixed schedule, before stock actually runs out.")
            self._row(fl, "Emergency (ESO)",
                      "Yes" if block.emergency_spare_ordering else "No",
                      "Emergency Spare Ordering (ESO): rush-order a spare "
                      "(usually faster but costlier) when the pool runs "
                      "out and a block needs one now.")
            self._row(fl, "Emergency time", f"{block.emer_time:.3f}",
                      "How long an emergency (ESO) spare takes to arrive.")
            self._row(fl, "Stock level (SLO)",
                      "Yes" if block.stock_level_ordering else "No",
                      "Stock-Level Ordering (SLO): automatically reorder "
                      "once the pool's stock drops to a threshold level.")
            if block.stock_level_ordering:
                self._row(fl, "SLO level", block.slo_order_level,
                          "Stock level that triggers an SLO reorder.")
                self._row(fl, "SLO quantity", block.slo_new_spares,
                          "How many spares an SLO reorder brings in.")
                self._row(fl, "SLO time", f"{block.slo_time:.3f}",
                          "How long an SLO reorder takes to arrive.")

        # Logistical Constraints (legacy Maintenance Information tab):
        # pre/post-repair logistics delay distributions
        gb, fl = self._group("Logistical Constraints", layout)
        ldt_tip = (
            "Logistics Delay Time (LDT): time spent waiting for parts, "
            "technicians, or transport -- separate from the repair work "
            "itself.\nPre-repair delay = LDT before the repair starts.\n"
            "Post-repair delay = LDT after the repair finishes (before "
            "the block goes back up).")
        gb.setToolTip(ldt_tip)
        bid3 = block.id
        # DELEGATED (review E-14); a failure law, so no code-18 relabel.
        from raptor.gui.ui.distro_combo import fill_distro_combo
        pre_combo = fill_distro_combo(QComboBox(), block.pre_dist,
                                  is_failure=True, kind="delay")
        pre_combo.setToolTip(ldt_tip)
        pre_combo.activated.connect(
            lambda _i, b=bid3, cmb=pre_combo:
            self.block_pre_ldt_dist_changed.emit(b, cmb.currentData()))
        fl.addRow("Pre-repair delay (LDT):", pre_combo)
        self._distribution_params_row(
            fl, block.pre_dist, "Pre-LDT", block.pre_params,
            lambda vals, b=bid3:
            self.block_pre_ldt_params_changed.emit(b, vals),
            addr_prefix=f"block:{bid3}:PreLDT",
            lambda_mode=False,       # Raptor's generic "other" box: no lambda
        )
        # DELEGATED (review E-14); a failure law, so no code-18 relabel.
        from raptor.gui.ui.distro_combo import fill_distro_combo
        post_combo = fill_distro_combo(QComboBox(), block.post_dist,
                                  is_failure=True, kind="delay")
        post_combo.setToolTip(ldt_tip)
        post_combo.activated.connect(
            lambda _i, b=bid3, cmb=post_combo:
            self.block_post_ldt_dist_changed.emit(b, cmb.currentData()))
        fl.addRow("Post-repair delay (LDT):", post_combo)
        self._distribution_params_row(
            fl, block.post_dist, "Post-LDT", block.post_params,
            lambda vals, b=bid3:
            self.block_post_ldt_params_changed.emit(b, vals),
            addr_prefix=f"block:{bid3}:PostLDT",
            lambda_mode=False,       # Raptor's generic "other" box: no lambda
        )

        gb, fl = self._group("Resources", layout)
        gb.setToolTip(
            "A resource (e.g. technicians) a block borrows during "
            "repair/PM and returns afterward -- a fixed shared pool, "
            "unlike a spare (consumed permanently). A block uses at "
            "most one resource pool.")
        res_combo = QComboBox()
        res_combo.addItem("(none)", "")
        res_sec = (self._rbd.sections.get("RESOURCE_DATA")
                  if self._rbd else None)
        for r5 in (res_sec.rows if res_sec else []):
            row_name = r5.get("PoolName", "")
            if row_name:
                res_combo.addItem(row_name, row_name)
        rci = res_combo.findText(block.resource_pool_name or "")
        res_combo.setCurrentIndex(max(rci, 0))
        res_combo.activated.connect(
            lambda _i, b=block.id, cmb=res_combo:
            self.block_resource_pool_changed.emit(b, cmb.currentData()))
        fl.addRow("Pool:", res_combo)

        from PySide6.QtWidgets import QSpinBox as _QSpinBox
        repair_spin = _QSpinBox()
        repair_spin.setRange(0, 999)
        repair_spin.setValue(int(block.repair_resources_needed))
        pm_spin = _QSpinBox()
        pm_spin.setRange(0, 999)
        pm_spin.setValue(int(block.pm_resources_needed))
        # The PM count only matters when the block performs PM (the
        # engine gates #Res1pmRep on usesPM) -- inert otherwise, so
        # grey it out with the reason instead of inviting an edit that
        # does nothing. Toggling the PM checkbox re-populates the whole
        # inspector (MainWindow's block_pm_enabled_changed handler),
        # so this gate tracks the checkbox live.
        if not block.uses_pm:
            pm_spin.setEnabled(False)
            pm_spin.setToolTip(
                "Only used when the block performs Preventive "
                "Maintenance -- enable 'This block performs "
                "Preventive Maintenance' on the PM tab first.")

        def _commit_res_counts(bid=block.id, rs=repair_spin, ps=pm_spin):
            self.block_resource_counts_changed.emit(
                bid, [rs.value(), ps.value()])
        repair_spin.editingFinished.connect(_commit_res_counts)
        pm_spin.editingFinished.connect(_commit_res_counts)
        fl.addRow("Needed for repair:", repair_spin)
        fl.addRow("Needed for PM:", pm_spin)
        manage_res_btn = QPushButton("Manage Resources…")
        manage_res_btn.setToolTip(
            "Create, edit or delete resource pools")
        manage_res_btn.clicked.connect(
            lambda: self.manage_pool_requested.emit("RESOURCE_DATA"))
        fl.addRow(manage_res_btn)

    def _populate_pm(self, block: RBDBlock):
        w = self._scroll_pm.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_pm)
        block_id = block.id

        # Raptor 7: "engage the checkbox at the top of this tab...
        # Once this checkbox is marked, preventive maintenance
        # activities... can be defined". Re-populates the whole tab on
        # toggle (matching the Node full-flow/cold-standby checkboxes'
        # pattern), since it gates whether the rest of the tab exists at
        # all.
        gb, fl = self._group("Preventive Maintenance", layout)
        pm_cb = _WordWrapCheckBox("This block performs Preventive "
                                  "Maintenance")
        pm_cb.setChecked(bool(block.uses_pm))
        pm_cb.toggled.connect(
            lambda on, bid=block_id: self.block_pm_enabled_changed.emit(
                bid, on))
        fl.addRow(pm_cb)

        if not block.uses_pm:
            return

        gb, fl = self._group("PM Configuration", layout)
        gb.setToolTip(
            "PM = Preventive Maintenance: scheduled servicing performed "
            "on a healthy block, separate from repairing a failure.")
        self._float_row(fl, "Frequency", block.pm_freq,
                         lambda v: self.block_pm_freq_changed.emit(block_id, v),
                         is_time=True)
        # Wording matches Raptor 7's PM tab ("At start-up PM cycle
        # has already elapsed"); "Stagger time" would be accurate but not
        # what a Raptor-trained user would search for.
        self._float_row(
            fl, "At start-up, PM cycle has already elapsed",
            block.pm_stagger,
            lambda v: self.block_pm_stagger_changed.emit(block_id, v),
            is_time=True)
        # Triggered: editable -- checkbox + trigger-name combo fed from
        # the model's TRIGGERS rows; writes pmTriggered/pmTrig
        trig_cb = _WordWrapCheckBox("Triggered by a PM trigger")
        trig_cb.setChecked(bool(block.pm_triggered))
        trig_combo = QComboBox()
        trig_combo.addItem("(none)", "")
        trig_sec = (self._rbd.sections.get("TRIGGERS")
                    if self._rbd else None)
        for r2 in (trig_sec.rows if trig_sec else []):
            type_name = r2.get("TriggerName", "")
            if type_name:
                trig_combo.addItem(type_name, type_name)
        if block.pm_trig:
            ti2 = trig_combo.findText(block.pm_trig)
            trig_combo.setCurrentIndex(max(ti2, 0))
        trig_combo.setEnabled(block.pm_triggered)

        def _trig_toggled(on: bool):
            self.block_pm_trig_changed.emit(
                block_id, on, trig_combo.currentText())
            trig_combo.setEnabled(on)
        def _trig_name(chosen: str):
            if block.pm_triggered:
                self.block_pm_trig_changed.emit(
                    block_id, True, chosen)
        trig_cb.toggled.connect(_trig_toggled)
        trig_combo.activated.connect(lambda _i: _trig_name(
            trig_combo.currentText()))
        fl.addRow(trig_cb)
        fl.addRow("Trigger name:", trig_combo)
        manage_trig_btn = QPushButton("Manage Triggers…")
        manage_trig_btn.setToolTip(
            "Create, edit or delete triggers")
        manage_trig_btn.clicked.connect(
            lambda: self.manage_pool_requested.emit("TRIGGERS"))
        fl.addRow(manage_trig_btn)

        gb, fl = self._group("PM Distribution", layout)
        # DELEGATED (review E-14); a PM law, so no code-18 relabel.
        from raptor.gui.ui.distro_combo import fill_distro_combo
        dist_combo = fill_distro_combo(QComboBox(), block.pm_dist,
                                       is_failure=True, kind="delay")
        dist_combo.currentIndexChanged.connect(
            lambda _i, bid=block.id, cmb=dist_combo:
            self.block_pm_dist_changed.emit(bid, cmb.currentData()))
        fl.addRow("Type:", dist_combo)
        self._distribution_params_row(
            fl, block.pm_dist, "PM", block.pm_params,
            lambda vals, bid=block.id:
            self.block_pm_params_changed.emit(bid, vals),
            addr_prefix=f"block:{block.id}:PM",
            lambda_mode=False,       # Raptor's generic "other" box: no lambda
        )

        gb, fl = self._group("PM Options", layout)
        for label, attr, key, tip in (
                ("Needs a spare to conduct PM", "pm_spare_needed",
                 "spare_needed", None),
                ("After PM is completed, refresh block like new",
                 "pm_refresh", "refresh",
                 "Ticked: every PM leaves the block as good as new, "
                 "whatever its restoration setting. Unticked: the block "
                 "resumes the life it had, or, with restoration Type I or "
                 "II (Failure tab), has part of its age removed."),
                ("Defer PM until a non-mission phase occurs",
                 "pm_mis_defer", "mis_defer",
                 "Only meaningful when this model has phases defined."),
                ("Defer PM until a spare or resources are available",
                 "pm_req_defer", "req_defer", None),
                ("A failure resets the time to next PM activity",
                 "pm_fail_reset", "fail_reset", None)):
            cb = _WordWrapCheckBox(label)
            cb.setChecked(bool(getattr(block, attr)))
            if tip:
                cb.setToolTip(tip)
            cb.toggled.connect(
                lambda on, bid=block_id, k=key:
                    self.block_pm_option_changed.emit(bid, k, on))
            fl.addRow(cb)

    def _populate_deps(self, block: RBDBlock):
        w = self._scroll_deps.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_deps)
        block_id = block.id

        # Master-element selector: `block.dependency_num`/`dep_type` use
        # the same encoding `_dependency_group` builds for Nodes
        # (0=independent, -1=locally, -2=system, >0=dependent-on-id), per
        # `dependency.py`'s block handling. The real Block
        # Properties Dependency tab matches the Node Properties
        # Dependency tab, so this exposes the same master-selector value.
        self._dependency_group(layout, block_id, block.dependency_num,
                               block.dep_type,
                               emit=self.block_dep_master_changed.emit)

        gb, fl = self._group("Default Dependency Action", layout)
        gb.setToolTip(
            "What this block does while waiting on its master element, "
            "before any dependency effect is actually applied.")
        r_op = QRadioButton("Operating state")
        r_idle = QRadioButton("Idle state")
        group = QButtonGroup(gb)
        group.addButton(r_op)
        group.addButton(r_idle)
        (r_idle if block.def_dep_state_idle else r_op).setChecked(True)

        def _send_default_dep_state(on, bid=block_id):
            # Both radios route through this one handler, which reads the
            # group's actual current state directly rather than trusting
            # either radio's own toggled `on` argument. Connecting only
            # r_idle.toggled would miss the `on=False` case: selecting
            # "Operating state" unchecks r_idle and would send nothing.
            if r_idle.isChecked() or r_op.isChecked():
                self.block_dep_default_idle_changed.emit(
                    bid, r_idle.isChecked())
        r_op.toggled.connect(_send_default_dep_state)
        r_idle.toggled.connect(_send_default_dep_state)
        fl.addRow(r_op)
        fl.addRow(r_idle)

        if block.dependency_num == 0:
            return

        gb, fl = self._group("Effects (%)", layout)
        dep_id = block.id
        fields: dict[str, QDoubleSpinBox] = {}

        def form_add(w: QWidget, label: str):
            fl.addRow(label + ":", w)

        for label, attr, cur in (
                ("Nothing %", "dep_nothing_percent", block.dep_nothing_percent),
                ("Idle %", "dep_idle_percent", block.dep_idle_percent),
                ("PM %", "dep_pm_percent", block.dep_pm_percent),
                ("Failure %", "dep_fail_percent", block.dep_fail_percent)):
            sp = QDoubleSpinBox()
            sp.setRange(0.0, 100.0)
            sp.setDecimals(2)
            sp.setValue(float(cur))
            fields[attr] = sp
            form_add(sp, label)
        thr = QDoubleSpinBox()
        thr.setRange(0.0, 1e9)
        thr.setDecimals(3)
        thr.setValue(float(block.dep_pm_threshold))
        form_add(thr, "PM threshold")

        apply_btn = QPushButton("Apply dependency")
        def apply_dep():
            percs = [fields[a].value() for a in
                     ("dep_nothing_percent", "dep_idle_percent",
                      "dep_pm_percent", "dep_fail_percent")]
            if abs(sum(percs) - 100.0) > 0.05:
                self.validation_failed.emit(
                    f"Dependency percentages must sum to 100 "
                    f"(got {sum(percs):.1f})")
                return
            self.block_dep_changed.emit(dep_id, percs +
                                        [thr.value()])
        apply_btn.clicked.connect(apply_dep)
        fl.addRow(apply_btn)

    def _populate_costs(self, block: RBDBlock):
        w = self._scroll_costs.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_costs)

        from raptor.gui.model_edit import (
            BLOCK_COST_DECIMALS, BLOCK_COST_FIELDS, BLOCK_COST_LIMIT,
            POOLED_COST_TIP, cost_field_readonly)
        fields = BLOCK_COST_FIELDS
        # Wording matches Raptor 7's own Costs tab rather than generic
        # column-name-derived labels -- e.g. "Rebate or disposal" not
        # "Done Fixed", "Recurring spare" not "Per spare".
        labels = ["Initial acquisition", "Running (/time)",
                  "Standby (/time)", "Idle downtime (/time)",
                  "Hands-on maintenance, fixed cost",
                  "Hands-on maintenance (/time)",
                  "Logistical hold (/time)",
                  "Preventive maintenance, fixed cost",
                  "Preventive maintenance (/time)",
                  "Preventive maintenance hold (/time)",
                  "Rebate or disposal, fixed cost",
                  "Permanently done (/time)",
                  "Recurring spare", "Emergency spare additional cost"]
        cost_id = block.id
        spins: list[QDoubleSpinBox] = []
        _gb, fl = self._group("Costs (edit all, then Apply)", layout)

        def form_add(w2, label):
            fl.addRow(label + ":", w2)

        for label, (attr, _col) in zip(labels, fields):
            sp = QDoubleSpinBox()
            sp.setRange(-BLOCK_COST_LIMIT, BLOCK_COST_LIMIT)
            sp.setDecimals(BLOCK_COST_DECIMALS)
            sp.setValue(float(getattr(block, attr)))
            if cost_field_readonly(block, attr):
                # The engine reads these two only for
                # SparingType.INFINITE (block_lifecycle.py); a Pooled
                # block's spare/emergency cost is the pool's own field
                # instead (Manage Spare Pools...), so editing here would
                # do nothing. Raptor 7 greys these out for the same
                # reason.
                sp.setEnabled(False)
                sp.setToolTip(POOLED_COST_TIP)
            spins.append(sp)
            form_add(sp, label)
        always_cb = QCheckBox("Always add Done cost at run end")
        always_cb.setChecked(bool(block.always_add_done_cost))

        def commit_always(on: bool, bid=block.id):
            self.block_always_done_changed.emit(bid, on)
        always_cb.toggled.connect(commit_always)
        fl.addRow(always_cb)

        def apply_costs():
            vals = [sp.value() for sp in spins]
            self.block_costs_changed.emit(cost_id, vals)

        apply_btn = QPushButton("Apply costs")
        apply_btn.clicked.connect(apply_costs)
        fl.addRow(apply_btn)
