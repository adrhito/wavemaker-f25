"""Operate: the whole job on one screen.

Select pistons on the tank, set the two numbers that matter, press Start.

This replaces the Control Home and Define Motors tabs, which between them made
running the machine cost six clicks, a drag and a tab switch, and required four
concepts before anything moved: sets, preparing, homing, and eighteen
parameters presented at once.

What is left:

* **Selecting pistons is the whole of "choosing what runs."** There is no
  "create set" step. The selection *is* the group. Groups only become visible
  if you press "Add group", and only then does the word appear.
* **Start does whatever is needed.** Writes changed parameters, homes if the
  machine is not homed, then runs. Homing physically moves every piston and
  takes about a minute, so the first run asks before doing it; after that Start
  is immediate.
* **Two numbers up front.** Stroke and speed are what get changed between runs.
  The other fourteen parameters are behind "All parameters", where they do not
  crowd out the ones in daily use.
"""

from __future__ import annotations

import os
from logging import Logger, getLogger
from tkinter import StringVar, messagebox, ttk
from typing import Dict, List, Optional

from app import params
from Model import MachineState, Model, MotorSet, RunMode
from modules.logging.log_utils import LOGGER_NAME
from modules.tank_view import TankView, describe_place
from modules.tooltip import Tooltip
from modules.widgets import RoundedButton, Segmented
from operate.ParameterDialog import ParameterDialog
from operate.PatternDialog import PatternDialog
from style import theme

MIXED = "--"


class Operate:
    """The Operate tab."""

    logger: Logger = getLogger(LOGGER_NAME)

    def __init__(self, root: ttk.Notebook, model: Model, view) -> None:
        self.model = model
        self.view = view
        self.root = root
        self.tab = ttk.Frame(root)

        self.editing: Optional[MotorSet] = None
        self._refreshing = False
        self._invalid: Dict[str, str] = {}

        self.tab.columnconfigure(0, weight=1)
        self.tab.rowconfigure(1, weight=1)

        self._build_header()

        body = ttk.Frame(self.tab, padding=(theme.GUTTER, 0, theme.GUTTER, theme.GAP))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_tank(body)
        self._build_controls(body)

        self.refresh(model.state)
        root.add(self.tab, text="  Operate  ")

    # -- construction ---------------------------------------------------------

    def _build_header(self) -> None:
        header = ttk.Frame(self.tab, padding=(theme.GUTTER, 14, theme.GUTTER, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        self.connection_label = ttk.Label(header, text="", style="Dim.TLabel")
        self.connection_label.grid(row=0, column=0, sticky="w")

        actions = ttk.Frame(header)
        actions.grid(row=0, column=2, sticky="e")
        self.reconnect_button = RoundedButton(
            actions, "Reconnect", self.reconnect, size="small", width=92
        )
        self.studio_button = RoundedButton(
            actions, "Open Studio 5000", self.open_studio, size="small", width=130
        )
        self.reconnect_button.grid(row=0, column=0, padx=(0, theme.TIGHT))
        self.studio_button.grid(row=0, column=1)

    def _build_tank(self, parent) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=2)
        card.grid(row=0, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)

        self.tank = TankView(
            card,
            on_select=self._on_tank_select,
            on_hover=self._on_tank_hover,
            height=250,
        )
        self.tank.grid(row=0, column=0, sticky="nsew")

        strip = ttk.Frame(parent)
        strip.grid(row=1, column=0, sticky="ew", pady=(theme.TIGHT, 0))
        strip.columnconfigure(1, weight=1)

        self.selection_label = ttk.Label(strip, text="", style="Value.TLabel")
        self.selection_label.grid(row=0, column=0, sticky="w")
        self.hover_label = ttk.Label(strip, text=" ", style="Dim.TLabel")
        self.hover_label.grid(row=0, column=1, sticky="e")

    def _build_controls(self, parent) -> None:
        card = ttk.Frame(parent, style="Card.TFrame",
                         padding=(theme.GUTTER, theme.GAP + 4, theme.GUTTER, theme.GAP + 4))
        card.grid(row=2, column=0, sticky="ew", pady=(theme.GAP, 0))
        card.columnconfigure(4, weight=1)

        # -- the two numbers that matter -------------------------------------
        ttk.Label(card, text="STROKE", style="CardDim.TLabel").grid(
            row=0, column=0, sticky="w", columnspan=3
        )
        self.pos1_var = StringVar()
        self.pos2_var = StringVar()
        self.pos1_entry = ttk.Entry(card, textvariable=self.pos1_var, width=6,
                                    justify="center")
        self.pos2_entry = ttk.Entry(card, textvariable=self.pos2_var, width=6,
                                    justify="center")
        self.pos1_entry.grid(row=1, column=0, pady=(4, 0))
        ttk.Label(card, text="to", style="CardDim.TLabel").grid(
            row=1, column=1, padx=theme.TIGHT, pady=(4, 0)
        )
        self.pos2_entry.grid(row=1, column=2, pady=(4, 0))
        ttk.Label(card, text="mm", style="CardDim.TLabel").grid(
            row=1, column=3, padx=(theme.TIGHT, theme.GUTTER), pady=(4, 0), sticky="w"
        )

        speed = ttk.Frame(card, style="Card.TFrame")
        speed.grid(row=0, column=4, rowspan=2, sticky="w")
        ttk.Label(speed, text="SPEED", style="CardDim.TLabel").grid(
            row=0, column=0, sticky="w", columnspan=2
        )
        self.speed_var = StringVar()
        self.speed_entry = ttk.Entry(speed, textvariable=self.speed_var, width=6,
                                     justify="center")
        self.speed_entry.grid(row=1, column=0, pady=(4, 0))
        ttk.Label(speed, text="mm/s", style="CardDim.TLabel").grid(
            row=1, column=1, padx=(theme.TIGHT, 0), pady=(4, 0), sticky="w"
        )

        # -- everything else --------------------------------------------------
        more = ttk.Frame(card, style="Card.TFrame")
        more.grid(row=0, column=5, rowspan=2, sticky="e")
        self.params_button = RoundedButton(
            more, "All parameters", self.open_parameters, size="small", width=120
        )
        self.pattern_button = RoundedButton(
            more, "Pattern", self.open_pattern, size="small", width=86
        )
        self.group_button = RoundedButton(
            more, "Add group", self.add_group, size="small", width=92
        )
        for column, button in enumerate(
            (self.params_button, self.pattern_button, self.group_button)
        ):
            button.set_background(theme.SURFACE)
            button.grid(row=0, column=column, padx=(theme.TIGHT, 0))

        Tooltip(self.params_button.canvas,
                "The other fourteen parameters: acceleration, jerk, profile,\n"
                "dwell times and the curve settings.")
        Tooltip(self.pattern_button.canvas,
                "Vary a parameter across the array -- a stagger front to back\n"
                "makes the wave travel along the chamber.")
        Tooltip(self.group_button.canvas,
                "Run a second group of pistons with different parameters\n"
                "at the same time.")

        self.problem_label = ttk.Label(card, text="", style="CardDim.TLabel",
                                       wraplength=900, justify="left")
        self.problem_label.grid(row=2, column=0, columnspan=6, sticky="w",
                                pady=(theme.GAP, 0))

        # -- group selector, hidden until there is more than one --------------
        self.group_row = ttk.Frame(card, style="Card.TFrame")
        self.group_row.grid(row=3, column=0, columnspan=6, sticky="w",
                            pady=(theme.GAP, 0))
        ttk.Label(self.group_row, text="Editing", style="CardDim.TLabel").grid(
            row=0, column=0, padx=(0, theme.TIGHT)
        )
        self.group_var = StringVar()
        self.group_box = ttk.Combobox(self.group_row, textvariable=self.group_var,
                                      state="readonly", width=26)
        self.group_box.grid(row=0, column=1, padx=(0, theme.TIGHT))
        self.group_box.bind("<<ComboboxSelected>>", self._on_group_chosen)
        self.delete_group_button = RoundedButton(
            self.group_row, "Delete group", self.delete_group, size="small", width=104
        )
        self.delete_group_button.set_background(theme.SURFACE)
        self.delete_group_button.grid(row=0, column=2)
        self.group_row.grid_remove()

        # -- run --------------------------------------------------------------
        run = ttk.Frame(parent)
        run.grid(row=3, column=0, sticky="ew", pady=(theme.GAP, 0))
        run.columnconfigure(2, weight=1)

        self.mode = Segmented(
            run,
            [(RunMode.SINGLE, "One stroke"), (RunMode.CONTINUOUS, "Continuous"),
             (RunMode.CURVE, "Curve")],
            width=280,
        )
        self.mode.grid(row=0, column=0, sticky="w")

        self.start_button = RoundedButton(
            run, "Start", self.start, variant="primary", size="large", width=190
        )
        self.start_button.grid(row=0, column=1, padx=(theme.GAP, 0))

        self.reset_button = RoundedButton(
            run, "Off and reset", self.reset, variant="ghost", size="small", width=110
        )
        self.reset_button.grid(row=0, column=3, sticky="e")

        for var in (self.pos1_var, self.pos2_var, self.speed_var):
            var.trace_add("write", lambda *_a: self._on_quick_param())

    # -- selection ------------------------------------------------------------

    def _on_tank_select(self, axes: List[int], additive: bool) -> None:
        owned = [a for a in axes if self.model.axis_owner(a) is not None]

        if len(axes) == 1:
            axis = axes[0]
            owner = self.model.axis_owner(axis)
            if owner is not None and owner is not self._implicit_group():
                self.editing = owner
                self._sync_group_box()
                self._load_values()
                self.refresh(self.model.state)
                return
            self.model.toggle(axis, not self.model.selection[axis])
        else:
            free = [a for a in axes if self.model.axis_owner(a) is None
                    or self.model.axis_owner(a) is self._implicit_group()]
            if additive:
                free = sorted(set(free) | set(self.model.selected_axes()))
            self.model.set_selection(free)
            if owned and not additive:
                blocked = [a for a in owned
                           if self.model.axis_owner(a) is not self._implicit_group()]
                if blocked:
                    self._say("{0} piston(s) belong to another group and were "
                              "left alone.".format(len(blocked)))

        self.refresh(self.model.state)

    def _implicit_group(self) -> Optional[MotorSet]:
        if self.model._implicit_group and self.model.sets:
            return self.model.sets[0]
        return None

    def _on_tank_hover(self, axis: Optional[int]) -> None:
        if axis is None:
            self.hover_label.configure(text=" ")
            return
        owner = self.model.axis_owner(axis)
        where = "Piston {0}  ·  {1}".format(axis, describe_place(axis))
        if owner is not None:
            position = self.tank.positions.get(axis)
            where += "  ·  {0}".format(owner.name)
            if position is not None:
                where += "  ·  {0:.0f} mm".format(position)
        self.hover_label.configure(text=where)

    # -- parameters -----------------------------------------------------------

    def _target(self) -> Optional[MotorSet]:
        """The group the quick fields edit."""
        if self.editing is not None and self.editing in self.model.sets:
            return self.editing
        return self.model.sets[0] if self.model.sets else None

    def _on_quick_param(self) -> None:
        if self._refreshing:
            return
        group = self._target()
        pairs = (
            ("Position 1", self.pos1_var),
            ("Position 2", self.pos2_var),
            ("Speed 1", self.speed_var),
        )
        for name, var in pairs:
            text = var.get()
            if text.strip() in ("", "-", MIXED):
                continue
            try:
                value = params.parse(name, text)
            except ValueError as exc:
                self._invalid[name] = str(exc)
                continue
            self._invalid.pop(name, None)
            if group is not None:
                group.set_param(name, value)
                if name == "Speed 1":
                    group.set_param("Speed 2", value)
                self.model.mark_unprepared()
            else:
                self.model.set_pending_param(name, value)
                if name == "Speed 1":
                    self.model.set_pending_param("Speed 2", value)
        self._show_problems()
        self.tank.show_strokes(self._strokes())

    def _load_values(self) -> None:
        self._refreshing = True
        try:
            group = self._target()
            for name, var in (
                ("Position 1", self.pos1_var),
                ("Position 2", self.pos2_var),
                ("Speed 1", self.speed_var),
            ):
                if group is None:
                    var.set(str(self.model.pending_params[name]))
                else:
                    value = group.common_value(name)
                    var.set(MIXED if value is None else str(value))
        finally:
            self._refreshing = False

    def _show_problems(self) -> None:
        self.problem_label.configure(
            text="  ".join(sorted(self._invalid.values())) if self._invalid else ""
        )

    def _say(self, message: str) -> None:
        self.problem_label.configure(text=message)

    # -- actions --------------------------------------------------------------

    def start(self) -> None:
        if not self.model.sets:
            self._say("Choose pistons on the tank above first.")
            return
        if self._invalid:
            self._show_problems()
            return

        mode = self.mode.get()
        if self.model.needs_homing:
            if not messagebox.askyesno(
                "Home the pistons first?",
                "The pistons need to be homed before they can run.\n\n"
                "Every selected piston will travel to its home position. "
                "This takes about a minute.\n\nContinue?",
                parent=self.tab,
            ):
                return
        self.model.run(mode)

    def reset(self) -> None:
        if messagebox.askyesno(
            "Off and reset",
            "Turn the motors off, clear the selection and start again?",
            parent=self.tab,
        ):
            self.model.reset()
            self.editing = None

    def add_group(self) -> None:
        try:
            self.model.add_group()
        except ValueError as exc:
            self._say(str(exc))
            return
        self.editing = None
        self.refresh(self.model.state)
        self._say("Now select the pistons for the next group.")

    def delete_group(self) -> None:
        group = self._target()
        if group is None:
            return
        if not messagebox.askyesno(
            "Delete group", "Delete {0}?".format(group.name), parent=self.tab
        ):
            return
        self.model.remove_set(group)
        self.editing = None
        self.refresh(self.model.state)

    def open_parameters(self) -> None:
        group = self._target()
        if group is None:
            self._say("Choose pistons on the tank above first.")
            return
        ParameterDialog(self.tab, group, self._after_dialog)

    def open_pattern(self) -> None:
        group = self._target()
        if group is None:
            self._say("Choose pistons on the tank above first.")
            return
        PatternDialog(self.tab, group, self._apply_pattern)

    def _after_dialog(self) -> None:
        self.model.mark_unprepared()
        self._load_values()
        self.refresh(self.model.state)

    def _apply_pattern(self, param: str, result) -> None:
        group = self._target()
        if group is None:
            return
        for axis, value in result.values.items():
            motor = group.motors.get(axis)
            if motor is not None:
                motor.set_param(param, value)
        self.model.mark_unprepared()
        self._load_values()
        self.refresh(self.model.state)
        self.view.status(
            "{0} across {1}: {2} distinct value(s).".format(
                param, group.name, result.distinct
            )
        )
        if result.clamped:
            messagebox.showwarning(
                "Some values were clamped",
                "These reached the limit for {0} and were held there:\n\n{1}".format(
                    param, "\n".join(result.clamped)
                ),
                parent=self.tab,
            )

    def reconnect(self) -> None:
        self.view.set_status_text("Looking for the PLC...")
        self.model.reconnect()

    def open_studio(self) -> None:
        from app import external

        if not external.open_studio_5000():
            messagebox.showerror(
                "Studio 5000 project not found",
                "Expected the project at:\n{0}".format(external.STUDIO_PROJECT),
                parent=self.tab,
            )
            return
        messagebox.showinfo(
            "Studio 5000",
            "Go Online, then put the controller in Rem Run.\n"
            "Come back here and press Reconnect.",
            parent=self.tab,
        )

    # -- progress -------------------------------------------------------------

    def show_progress(self, fraction: float, label: str) -> None:
        self.view.show_progress(fraction, label)

    def finish_progress(self, artifact: Optional[str]) -> None:
        self.view.hide_progress()
        if artifact and messagebox.askyesno(
            "Recording finished", "Open the analytics file?", parent=self.tab
        ):
            try:
                os.startfile(artifact)  # noqa: S606 - the app's own output
            except OSError as exc:
                self.logger.error("Could not open %s: %s", artifact, exc)

    def show_positions(self, readings: Dict[int, float]) -> None:
        self.tank.show_positions(readings)
        if self.model.unreadable_axes:
            self.tank.show_faults(self.model.unreadable_axes)

    # -- display --------------------------------------------------------------

    def onSelect(self) -> None:
        self.refresh(self.model.state)

    def _strokes(self):
        return dict(
            (m.axis, (m.write_params["Position 1"], m.write_params["Position 2"]))
            for m in self.model.all_motors
        )

    def _connection_text(self) -> str:
        if not getattr(self.model, "_connection_attempted", True):
            return "Looking for the wavemaker..."
        if self.model.is_live:
            return "Connected  ·  {0}".format(self.model.ip_address)
        if getattr(self.model, "_simulate", False):
            return "Mock wavemaker  ·  nothing physical will move"
        return "Not connected  ·  the PLC at {0} did not answer".format(
            self.model.ip_address
        )

    def _sync_group_box(self) -> None:
        labels = ["{0}  ({1} pistons)".format(s.name, len(s)) for s in self.model.sets]
        self.group_box["values"] = labels
        target = self._target()
        if target is not None and target in self.model.sets:
            self.group_var.set(labels[self.model.sets.index(target)])

    def _on_group_chosen(self, _event) -> None:
        index = self.group_box.current()
        if 0 <= index < len(self.model.sets):
            self.editing = self.model.sets[index]
            self._load_values()
            self.refresh(self.model.state)

    def refresh(self, state: MachineState) -> None:
        busy = state in (MachineState.PREPARING, MachineState.RUNNING)

        self.connection_label.configure(text=self._connection_text())
        deliberate = getattr(self.model, "_simulate", False)
        offline = (
            not self.model.is_live
            and getattr(self.model, "_connection_attempted", True)
            and not deliberate
        )
        for button in (self.reconnect_button, self.studio_button):
            if offline and not busy:
                button.canvas.grid()
            else:
                button.canvas.grid_remove()

        self.tank.show_sets(self.model.sets)
        self.tank.show_selection(self.model.selected_axes())
        self.tank.show_strokes(self._strokes())
        self.tank.interactive = not busy
        if state is not MachineState.RUNNING:
            self.tank.clear_positions()

        count = len(self.model.all_motors)
        if count == 0:
            self.selection_label.configure(
                text="No pistons selected  ·  click or drag on the tank"
            )
        elif len(self.model.sets) == 1:
            self.selection_label.configure(text="{0} pistons selected".format(count))
        else:
            self.selection_label.configure(
                text="{0} pistons in {1} groups".format(count, len(self.model.sets))
            )

        if len(self.model.sets) > 1:
            self.group_row.grid()
            self._sync_group_box()
        else:
            self.group_row.grid_remove()

        self._load_values()

        entry_state = "disabled" if busy else "normal"
        for entry in (self.pos1_entry, self.pos2_entry, self.speed_entry):
            entry["state"] = entry_state
        self.group_box["state"] = "disabled" if busy else "readonly"

        has_pistons = bool(self.model.sets)
        for button, enabled in (
            (self.params_button, has_pistons and not busy),
            (self.pattern_button, has_pistons and not busy),
            (self.group_button, has_pistons and not busy),
            (self.delete_group_button, has_pistons and not busy),
            (self.reset_button, has_pistons and not busy),
        ):
            button.set_state("normal" if enabled else "disabled")

        self.mode.set_state("disabled" if busy else "normal")
        self.start_button.set_state(
            "normal" if has_pistons and not busy else "disabled"
        )
        self.start_button.configure_text(
            "Homing..." if state is MachineState.PREPARING
            else "Running" if state is MachineState.RUNNING
            else "Start"
        )

        if busy:
            self._say("")
        else:
            self._show_problems()
