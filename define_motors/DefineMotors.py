"""Define Motors: choose pistons, group them into sets, give each set parameters.

Two ideas run this tab.

**Parameters belong to a set.** What you type goes only to the set named above
the boxes. The previous version applied every keystroke to every motor in every
set, so a second set could never differ from the first.

**You pick pistons on the picture, not from a list.** Thirty identical
checkboxes labelled "Motor 0".."Motor 29" meant counting along a row to find the
one you wanted, and the array was drawn differently over on Control Home. Both
screens now show the same tank.
"""

from __future__ import annotations

from logging import Logger, getLogger
from tkinter import StringVar, messagebox, ttk
from typing import Dict, List, Optional

from app import params
from define_motors.PatternDialog import PatternDialog
from Model import MachineState, Model, MotorSet
from modules.logging.log_utils import LOGGER_NAME
from modules.tank_view import TankView
from modules.tooltip import Tooltip

#: Shown when the pistons of a set disagree on a parameter, which a preset or a
#: pattern can cause. Typing over it sets them all to the new value.
MIXED = "(varies)"

PENDING = "New set - current selection"

#: The parameters, in the order they appear, grouped so the panel reads as
#: three short lists rather than one column of eighteen.
GROUPS = (
    ("Stroke", ["Position 1", "Position 2", "Move Type", "Profile"]),
    ("Speed and ramp", ["Speed 1", "Speed 2", "Accel 1", "Accel 2",
                        "Decel 1", "Decel 2", "Jerk 1", "Jerk 2"]),
    ("Timing and curve", ["Time 1", "Time 2", "Curve ID", "Time Scale",
                          "Amplitude Scale", "Curve Offset"]),
)


class DefineMotors:
    """The Define Motors tab."""

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

        header = ttk.Frame(self.tab, padding=(22, 16, 22, 0))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Define Motors", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.hint = ttk.Label(
            header,
            text="Click or drag on the tank to choose pistons, then press Create Set.",
            style="Dim.TLabel",
        )
        self.hint.grid(row=1, column=0, sticky="w", pady=(2, 0))

        body = ttk.Frame(self.tab, padding=(22, 12, 22, 16))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        self._build_tank(body)
        self._build_actions(body)
        self._build_params(body)

        self.refresh(model.state)
        root.add(self.tab, text=" Define Motors")

    # -- construction ---------------------------------------------------------

    def _build_tank(self, parent) -> None:
        self.tank = TankView(
            parent,
            on_select=self._on_tank_select,
            on_hover=self._on_tank_hover,
            height=210,
        )
        self.tank.grid(row=0, column=0, sticky="ew")

        self.hover_label = ttk.Label(parent, text=" ", style="Dim.TLabel")
        self.hover_label.grid(row=1, column=0, sticky="w", pady=(4, 10))

    def _build_actions(self, parent) -> None:
        row = ttk.Frame(parent)
        row.grid(row=2, column=0, sticky="new")
        row.columnconfigure(9, weight=1)

        self.create_button = ttk.Button(
            row, text="Create Set", width=13, command=self.create_set
        )
        self.select_all_button = ttk.Button(
            row, text="Select All", width=11, command=self.select_all_free
        )
        self.clear_button = ttk.Button(
            row, text="Clear", width=9, command=self.clear_selection
        )
        for column, button in enumerate(
            (self.create_button, self.select_all_button, self.clear_button)
        ):
            button.grid(row=0, column=column, padx=(0, 6))

        ttk.Separator(row, orient="vertical").grid(
            row=0, column=3, sticky="ns", padx=12
        )

        ttk.Label(row, text="Editing").grid(row=0, column=4, padx=(0, 6))
        self.set_var = StringVar(value=PENDING)
        self.set_box = ttk.Combobox(
            row, textvariable=self.set_var, state="readonly", width=30
        )
        self.set_box.grid(row=0, column=5, padx=(0, 6))
        self.set_box.bind("<<ComboboxSelected>>", self._on_set_chosen)

        self.pattern_button = ttk.Button(
            row, text="Pattern...", width=11, command=self.open_pattern
        )
        self.delete_button = ttk.Button(
            row, text="Delete Set", width=11, command=self.delete_set
        )
        self.pattern_button.grid(row=0, column=6, padx=(0, 6))
        self.delete_button.grid(row=0, column=7, padx=(0, 6))

        Tooltip(
            self.create_button,
            "Groups the selected pistons into a set with the parameters below.\n"
            "Each set keeps its own parameters and runs alongside the others.",
        )
        Tooltip(
            self.pattern_button,
            "Fill one parameter across this set in a shape: a ramp, a stagger\n"
            "per column for a travelling wave, or mirrored about the centre.",
        )

    def _build_params(self, parent) -> None:
        holder = ttk.Frame(parent)
        holder.grid(row=3, column=0, sticky="nsew", pady=(16, 0))
        holder.columnconfigure(0, weight=1)

        self.editing_label = ttk.Label(holder, text="", style="Heading2.TLabel")
        self.editing_label.grid(row=0, column=0, sticky="w", pady=(0, 10))

        grid = ttk.Frame(holder)
        grid.grid(row=1, column=0, sticky="w")

        self.param_vars: Dict[str, StringVar] = {}
        self.param_entries: Dict[str, ttk.Entry] = {}

        for column, (title, names) in enumerate(GROUPS):
            group = ttk.Frame(grid)
            group.grid(row=0, column=column, sticky="nw", padx=(0, 34))
            ttk.Label(group, text=title, style="Group.TLabel").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
            )
            for index, name in enumerate(names):
                spec = params.BY_NAME[name]
                label = ttk.Label(group, text=name)
                label.grid(row=index + 1, column=0, sticky="e", padx=(0, 8), pady=3)
                Tooltip(
                    label,
                    "{0}\n\nAccepted: {1}".format(spec.help, spec.describe_range()),
                )
                var = StringVar()
                entry = ttk.Entry(group, textvariable=var, width=11)
                entry.grid(row=index + 1, column=1, pady=3)
                var.trace_add(
                    "write", lambda *_a, _n=name: self._on_param_typed(_n)
                )
                self.param_vars[name] = var
                self.param_entries[name] = entry

        footer = ttk.Frame(holder)
        footer.grid(row=2, column=0, sticky="w", pady=(12, 0))
        self.param_message = ttk.Label(
            footer, text="", wraplength=760, justify="left"
        )
        self.param_message.grid(row=0, column=0, sticky="w")

        buttons = ttk.Frame(holder)
        buttons.grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Button(
            buttons, text="Restore Defaults", command=self.restore_defaults
        ).grid(row=0, column=0, padx=(0, 8))
        self.copy_button = ttk.Button(
            buttons, text="Copy to All Sets", command=self.copy_to_all_sets
        )
        self.copy_button.grid(row=0, column=1)
        Tooltip(
            self.copy_button,
            "Applies the values shown to every set. Use it when you deliberately\n"
            "want all sets to match.",
        )

    # -- tank interaction -----------------------------------------------------

    def _on_tank_select(self, axes: List[int], additive: bool) -> None:
        owned = [a for a in axes if self.model.axis_owner(a) is not None]
        free = [a for a in axes if self.model.axis_owner(a) is None]

        if owned and len(axes) == 1:
            owner = self.model.axis_owner(owned[0])
            # Clicking a piston that is already in a set selects that set for
            # editing, which is what you almost always want.
            self.editing = owner
            self._sync_set_box()
            self._load_param_values()
            self._refresh_editing_label()
            return

        if not additive and len(axes) > 1:
            for axis in range(30):
                self.model.toggle(axis, False)

        for axis in free:
            if len(axes) == 1:
                self.model.toggle(axis, not self.model.selection[axis])
            else:
                self.model.toggle(axis, True)

        if owned and len(axes) > 1:
            self.param_message.configure(
                text="Skipped {0} piston(s) already in a set.".format(len(owned))
            )

        self._paint()

    def _on_tank_hover(self, axis: Optional[int]) -> None:
        if axis is None:
            self.hover_label.configure(text=" ")
            return
        owner = self.model.axis_owner(axis)
        row, column = axis % 3 + 1, axis // 3 + 1
        where = "Motor {0}  -  row {1}, column {2}".format(axis, row, column)
        if owner is None:
            state = "selected" if self.model.selection[axis] else "free"
            self.hover_label.configure(text="{0}  -  {1}".format(where, state))
            return
        motor = owner.motors[axis]
        self.hover_label.configure(
            text="{0}  -  {1}  -  stroke {2} to {3} mm at {4} mm/s".format(
                where,
                owner.name,
                motor.write_params["Position 1"],
                motor.write_params["Position 2"],
                motor.write_params["Speed 1"],
            )
        )

    # -- selection ------------------------------------------------------------

    def select_all_free(self) -> None:
        for axis in range(30):
            if self.model.axis_owner(axis) is None:
                self.model.toggle(axis, True)
        self._paint()

    def clear_selection(self) -> None:
        for axis in range(30):
            self.model.toggle(axis, False)
        self._paint()

    def create_set(self) -> None:
        try:
            motor_set = self.model.create_set()
        except ValueError as exc:
            messagebox.showwarning("Cannot create set", str(exc), parent=self.tab)
            return
        self.editing = motor_set
        self.model.pending_params = params.defaults()
        self.refresh(self.model.state)
        self.view.status(
            "{0} created with {1} piston(s).".format(motor_set.name, len(motor_set))
        )

    def delete_set(self) -> None:
        if self.editing is None:
            messagebox.showinfo(
                "Nothing to delete",
                "Choose a set in the Editing list first.",
                parent=self.tab,
            )
            return
        name = self.editing.name
        if not messagebox.askyesno(
            "Delete set", "Delete {0}?".format(name), parent=self.tab
        ):
            return
        self.model.remove_set(self.editing)
        self.editing = None
        self.model.mark_unprepared()
        self.refresh(self.model.state)
        self.view.status("{0} deleted.".format(name))

    # -- parameters -----------------------------------------------------------

    def _target_description(self) -> str:
        if self.editing is not None:
            return "{0}  -  motors {1}".format(
                self.editing.name, ", ".join(str(a) for a in self.editing.axes)
            )
        selected = self.model.selected_axes()
        if selected:
            return "New set  -  motors {0}, not created yet".format(
                ", ".join(str(a) for a in selected)
            )
        return "New set  -  choose pistons on the tank above"

    def _refresh_editing_label(self) -> None:
        self.editing_label.configure(
            text="Parameters for {0}".format(self._target_description())
        )

    def _on_param_typed(self, name: str) -> None:
        if self._refreshing:
            return
        text = self.param_vars[name].get()
        if text.strip() in ("", "-", MIXED):
            return

        try:
            value = params.parse(name, text)
        except ValueError as exc:
            self._invalid[name] = str(exc)
            self._show_problems()
            return

        self._invalid.pop(name, None)
        if self.editing is not None:
            self.editing.set_param(name, value)
            self.model.mark_unprepared()
        else:
            self.model.set_pending_param(name, value)
        self._show_problems()
        self._paint_strokes()

    def _show_problems(self) -> None:
        self.param_message.configure(
            text="Not applied - " + "; ".join(sorted(self._invalid.values()))
            if self._invalid
            else ""
        )

    def restore_defaults(self) -> None:
        defaults = params.defaults()
        if self.editing is not None:
            for name, value in defaults.items():
                self.editing.set_param(name, value)
            self.model.mark_unprepared()
        else:
            self.model.pending_params = defaults
        self._invalid.clear()
        self._load_param_values()
        self._paint_strokes()

    def copy_to_all_sets(self) -> None:
        if not self.model.sets:
            return
        source = (
            dict(
                (spec.name, self.editing.common_value(spec.name))
                for spec in params.PARAMS
            )
            if self.editing is not None
            else dict(self.model.pending_params)
        )
        shared = dict((k, v) for k, v in source.items() if v is not None)
        if not shared:
            messagebox.showinfo(
                "Nothing to copy",
                "This set's pistons do not share a single set of values.",
                parent=self.tab,
            )
            return
        if not messagebox.askyesno(
            "Copy to all sets",
            "Apply these {0} values to all {1} set(s)?\n\n"
            "Any pattern applied to another set will be overwritten.".format(
                len(shared), len(self.model.sets)
            ),
            parent=self.tab,
        ):
            return
        for motor_set in self.model.sets:
            for name, value in shared.items():
                motor_set.set_param(name, value)
        self.model.mark_unprepared()
        self.refresh(self.model.state)
        self.view.status("Parameters copied to all sets.")

    def open_pattern(self) -> None:
        if self.editing is None:
            messagebox.showinfo(
                "Choose a set",
                "Patterns apply to a set. Create one, or pick an existing set "
                "in the Editing list.",
                parent=self.tab,
            )
            return
        PatternDialog(self.tab, self.editing, self._apply_pattern)

    def _apply_pattern(self, param: str, result) -> None:
        for axis, value in result.values.items():
            motor = self.editing.motors.get(axis)
            if motor is not None:
                motor.set_param(param, value)
        self.model.mark_unprepared()
        self._load_param_values()
        self._paint()
        message = "{0} across {1}: {2} distinct value(s).".format(
            param, self.editing.name, result.distinct
        )
        self.view.status(message)
        self.logger.info("Pattern applied - %s", result.summary(param).splitlines()[0])
        if result.clamped:
            messagebox.showwarning(
                "Some values were clamped",
                "These reached the limit for {0} and were held there:\n\n{1}".format(
                    param, "\n".join(result.clamped)
                ),
                parent=self.tab,
            )

    def _load_param_values(self) -> None:
        self._refreshing = True
        try:
            for spec in params.PARAMS:
                if self.editing is not None:
                    value = self.editing.common_value(spec.name)
                    text = MIXED if value is None else str(value)
                else:
                    text = str(self.model.pending_params[spec.name])
                self.param_vars[spec.name].set(text)
        finally:
            self._refreshing = False

    # -- painting -------------------------------------------------------------

    def _strokes(self) -> Dict[int, tuple]:
        strokes = {}
        for motor_set in self.model.sets:
            for motor in motor_set:
                strokes[motor.axis] = (
                    motor.write_params["Position 1"],
                    motor.write_params["Position 2"],
                )
        return strokes

    def _paint_strokes(self) -> None:
        self.tank.show_strokes(self._strokes())

    def _paint(self) -> None:
        self.tank.show_sets(self.model.sets)
        self.tank.show_selection(self.model.selected_axes())
        self._paint_strokes()
        self._refresh_editing_label()

    def _sync_set_box(self) -> None:
        labels = [PENDING] + [
            "{0}  ({1} motors)".format(s.name, len(s)) for s in self.model.sets
        ]
        self.set_box["values"] = labels
        if self.editing is not None and self.editing in self.model.sets:
            self.set_var.set(labels[self.model.sets.index(self.editing) + 1])
        else:
            self.editing = None
            self.set_var.set(PENDING)

    def _on_set_chosen(self, _event) -> None:
        index = self.set_box.current()
        self.editing = None if index <= 0 else self.model.sets[index - 1]
        self._invalid.clear()
        self._load_param_values()
        self._refresh_editing_label()
        self._show_problems()

    # -- lifecycle ------------------------------------------------------------

    def onSelect(self) -> None:
        self.refresh(self.model.state)

    def refresh(self, state: MachineState) -> None:
        busy = state in (MachineState.PREPARING, MachineState.RUNNING)

        self._sync_set_box()
        self._load_param_values()
        self._paint()

        for button in (
            self.create_button, self.select_all_button, self.clear_button,
            self.delete_button, self.pattern_button, self.copy_button,
        ):
            button["state"] = "disabled" if busy else "normal"
        self.set_box["state"] = "disabled" if busy else "readonly"
        for entry in self.param_entries.values():
            entry["state"] = "disabled" if busy else "normal"

        self.tank.interactive = not busy

        if busy:
            self.param_message.configure(
                text="The machine is busy. Stop it before changing motors or "
                "parameters."
            )
        else:
            self._show_problems()

    def update_stop_button_status(self) -> None:
        """Kept for compatibility; the stop control now lives in the status bar."""
