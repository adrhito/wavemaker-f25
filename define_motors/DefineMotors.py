"""Define Motors: choose pistons, group them into sets, and set parameters.

The important idea on this tab is that **parameters belong to a set**.  You pick
what you are editing in the "Editing" list on the left, and what you type goes
only there.  The previous version applied every keystroke to every motor in
every set at once, so a second set could never differ from the first -- which
defeated the whole point of having sets.
"""

from __future__ import annotations

from functools import partial
from logging import Logger, getLogger
from tkinter import Checkbutton, IntVar, Listbox, StringVar, messagebox, ttk
from typing import Dict, List, Optional

from app import params
from Model import MachineState, Model, MotorSet
from modules.logging.log_utils import LOGGER_NAME
from modules.tooltip import Tooltip

#: Shown in an entry box when the pistons of a set disagree on that parameter,
#: which a preset can cause. Typing over it sets them all to the new value.
MIXED = "(varies)"

PENDING_LABEL = "New set (current selection)"

FREE_COLOUR = "#d9d9d9"
SELECTED_COLOUR = "#7ed957"
IN_SET_COLOUR = "#f7a1c4"


class DefineMotors:
    """The Define Motors tab."""

    logger: Logger = getLogger(LOGGER_NAME)

    def __init__(self, root: ttk.Notebook, model: Model, view) -> None:
        self.model = model
        self.view = view
        self.root = root
        self.tab = ttk.Frame(root)

        #: Which set the parameter boxes are editing; ``None`` means the
        #: not-yet-grouped selection.
        self.editing: Optional[MotorSet] = None
        #: Set while the boxes are being refreshed, so filling them in does not
        #: read back as the operator typing.
        self._refreshing = False
        self._invalid: Dict[str, str] = {}

        self._drag_origin: Optional[tuple] = None

        self.title_frame = ttk.Frame(self.tab, padding=(25, 20, 25, 0))
        self.content_frame = ttk.Frame(self.tab, padding=25)
        self.title_frame.grid(row=0, column=0, sticky="w")
        self.content_frame.grid(row=1, column=0, sticky="nsew")

        ttk.Label(
            self.title_frame, text="Define Motors", style="Heading.TLabel"
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            self.title_frame,
            text="Tick motors, press Create Set, then give that set its parameters.",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        self._build_motor_grid()
        self._build_set_controls()
        self._build_param_frame()

        self.refresh(model.state)
        root.add(self.tab, text=" Define Motors")

    # -- construction ---------------------------------------------------------

    def _build_motor_grid(self) -> None:
        frame = ttk.Frame(self.content_frame, borderwidth=10)
        frame.grid(row=0, column=0, columnspan=2, sticky="w")
        self.motor_frame = frame

        self.check_vars: List[IntVar] = [IntVar() for _ in range(30)]
        self.check_buttons: List[Checkbutton] = []
        self.check_tips: List[Tooltip] = []

        for axis in range(30):
            button = Checkbutton(
                frame,
                text="Motor {0}".format(axis),
                variable=self.check_vars[axis],
                command=partial(self.on_check, axis),
            )
            button.grid(row=axis % 3 + 1, column=axis // 3 + 1, padx=(0, 10), pady=5)
            self.check_buttons.append(button)
            self.check_tips.append(Tooltip(button, "Not selected"))

        frame.bind("<Button-1>", self._drag_start)
        frame.bind("<ButtonRelease-1>", self._drag_end)
        root_widget = self.root
        root_widget.update_idletasks()

        actions = ttk.Frame(self.content_frame)
        actions.grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 20))

        self.create_button = ttk.Button(
            actions, text="Create Set from Selection", command=self.create_set
        )
        self.create_button.grid(row=0, column=0, padx=(0, 10))

        self.select_all_button = ttk.Button(
            actions, text="Select All Free", command=self.select_all_free
        )
        self.select_all_button.grid(row=0, column=1, padx=(0, 10))

        self.clear_selection_button = ttk.Button(
            actions, text="Clear Selection", command=self.clear_selection
        )
        self.clear_selection_button.grid(row=0, column=2, padx=(0, 10))

        Tooltip(
            self.create_button,
            "Groups the ticked motors into a set with the parameters shown below.\n"
            "Each set keeps its own parameters and can run alongside the others.",
        )

    def _build_set_controls(self) -> None:
        frame = ttk.Frame(self.content_frame)
        frame.grid(row=2, column=0, sticky="nw", padx=(0, 30))

        ttk.Label(frame, text="Editing").grid(row=0, column=0, sticky="w")
        self.set_list = Listbox(
            frame,
            height=9,
            width=32,
            exportselection=False,
            bg="#2b2b2b",
            fg="#e8e8e8",
            selectbackground="#777A7A",
            highlightthickness=0,
        )
        self.set_list.grid(row=1, column=0, sticky="w", pady=(4, 6))
        self.set_list.bind("<<ListboxSelect>>", self._on_set_selected)

        self.delete_button = ttk.Button(
            frame, text="Delete Selected Set", command=self.delete_set
        )
        self.delete_button.grid(row=2, column=0, sticky="w")

        self.reset_button = ttk.Button(
            frame, text="Turn Off and Reset All", command=self.reset_all
        )
        self.reset_button.grid(row=3, column=0, sticky="w", pady=(6, 0))

    def _build_param_frame(self) -> None:
        frame = ttk.Frame(self.content_frame)
        frame.grid(row=2, column=1, sticky="nw")
        self.param_frame = frame

        self.editing_label = ttk.Label(frame, text="", style="Step.TLabel")
        self.editing_label.grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 8))

        self.param_vars: Dict[str, StringVar] = {}
        self.param_entries: Dict[str, ttk.Entry] = {}

        for index, spec in enumerate(params.PARAMS):
            column, row = divmod(index, 6)
            label = ttk.Label(frame, text=spec.name)
            label.grid(row=row + 1, column=column * 2, padx=(0, 8), pady=6, sticky="e")
            Tooltip(label, "{0}\n\nAccepted: {1}".format(spec.help, spec.describe_range()))

            var = StringVar()
            entry = ttk.Entry(frame, textvariable=var, width=12)
            entry.grid(row=row + 1, column=column * 2 + 1, padx=(0, 24), pady=6)
            var.trace_add("write", partial(self._on_param_typed, spec.name))

            self.param_vars[spec.name] = var
            self.param_entries[spec.name] = entry

        self.param_message = ttk.Label(frame, text="", wraplength=560, justify="left")
        self.param_message.grid(row=7, column=0, columnspan=6, sticky="w", pady=(10, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=8, column=0, columnspan=6, sticky="w", pady=(10, 0))
        ttk.Button(buttons, text="Restore Defaults", command=self.restore_defaults).grid(
            row=0, column=0, padx=(0, 10)
        )
        self.copy_button = ttk.Button(
            buttons, text="Copy These to All Sets", command=self.copy_to_all_sets
        )
        self.copy_button.grid(row=0, column=1)
        Tooltip(
            self.copy_button,
            "Applies the values shown above to every set.\n"
            "Use this when you deliberately want all sets to match.",
        )

    # -- selection ------------------------------------------------------------

    def on_check(self, axis: int) -> None:
        """A checkbox was clicked."""
        owner = self.model.axis_owner(axis)
        if owner is not None:
            # Belongs to a set already; put the tick back and say why.
            self.check_vars[axis].set(0)
            self.param_message.configure(
                text="Motor {0} is already in {1}. Delete that set to free it.".format(
                    axis, owner.name
                )
            )
            return
        self.model.toggle(axis, bool(self.check_vars[axis].get()))
        self._paint_checkboxes()
        self._refresh_editing_label()

    def select_all_free(self) -> None:
        for axis in range(30):
            if self.model.axis_owner(axis) is None:
                self.check_vars[axis].set(1)
                self.model.toggle(axis, True)
        self._paint_checkboxes()
        self._refresh_editing_label()

    def clear_selection(self) -> None:
        for axis in range(30):
            self.check_vars[axis].set(0)
            self.model.toggle(axis, False)
        self._paint_checkboxes()
        self._refresh_editing_label()

    def create_set(self) -> None:
        try:
            motor_set = self.model.create_set()
        except ValueError as exc:
            messagebox.showwarning("Cannot create set", str(exc), parent=self.tab)
            return

        for axis in motor_set.axes:
            self.check_vars[axis].set(0)

        self.editing = motor_set
        self.model.pending_params = params.defaults()
        self.refresh(self.model.state)
        self.view.status("{0} created. Prepare the motors when ready.".format(motor_set.name))

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

    def reset_all(self) -> None:
        if not messagebox.askyesno(
            "Turn off and reset",
            "Turn the motors off, clear every set and return the application "
            "to its starting state?",
            parent=self.tab,
        ):
            return
        self.model.reset()
        self.editing = None
        self.clear_selection()
        self.refresh(self.model.state)

    # -- parameters -----------------------------------------------------------

    def _target_description(self) -> str:
        if self.editing is not None:
            return "{0} - motors {1}".format(
                self.editing.name, ", ".join(str(a) for a in self.editing.axes)
            )
        selected = self.model.selected_axes()
        if selected:
            return "New set - motors {0} (not created yet)".format(
                ", ".join(str(a) for a in selected)
            )
        return "New set - tick some motors above"

    def _refresh_editing_label(self) -> None:
        self.editing_label.configure(text="Parameters for: " + self._target_description())

    def _on_param_typed(self, name: str, *_args) -> None:
        """Apply a parameter as it is typed, to the set being edited only."""
        if self._refreshing:
            return

        text = self.param_vars[name].get()
        if text.strip() in ("", "-", MIXED):
            # A half-typed value is not an error; wait for the rest.
            return

        try:
            value = params.parse(name, text)
        except ValueError as exc:
            self._invalid[name] = str(exc)
            self._show_param_problems()
            return

        self._invalid.pop(name, None)

        if self.editing is not None:
            self.editing.set_param(name, value)
            self.model.mark_unprepared()
        else:
            self.model.set_pending_param(name, value)

        self._show_param_problems()
        self._update_tooltips()

    def _show_param_problems(self) -> None:
        if self._invalid:
            self.param_message.configure(
                text="Not applied: " + "; ".join(sorted(self._invalid.values()))
            )
        else:
            self.param_message.configure(text="")

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
        self._update_tooltips()

    def copy_to_all_sets(self) -> None:
        """Push the values on screen to every set."""
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
            return
        if not messagebox.askyesno(
            "Copy to all sets",
            "Apply these {0} values to all {1} sets?".format(
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

    def _load_param_values(self) -> None:
        """Fill the boxes from whatever is being edited."""
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

    def _paint_checkboxes(self) -> None:
        for axis in range(30):
            owner = self.model.axis_owner(axis)
            if owner is not None:
                self.check_buttons[axis]["bg"] = IN_SET_COLOUR
                self.check_tips[axis].updateText(
                    "In {0}\n\n{1}".format(
                        owner.name, owner.motors[axis].describe_params()
                    )
                )
            elif self.check_vars[axis].get():
                self.check_buttons[axis]["bg"] = SELECTED_COLOUR
                self.check_tips[axis].updateText("Selected, not yet in a set")
            else:
                self.check_buttons[axis]["bg"] = FREE_COLOUR
                self.check_tips[axis].updateText("Not selected")

    def _update_tooltips(self) -> None:
        for axis in range(30):
            owner = self.model.axis_owner(axis)
            if owner is not None:
                self.check_tips[axis].updateText(
                    "In {0}\n\n{1}".format(
                        owner.name, owner.motors[axis].describe_params()
                    )
                )

    def _rebuild_set_list(self) -> None:
        self.set_list.delete(0, "end")
        self.set_list.insert("end", PENDING_LABEL)
        for motor_set in self.model.sets:
            self.set_list.insert(
                "end", "{0}  ({1} motors)".format(motor_set.name, len(motor_set))
            )

        if self.editing is not None and self.editing in self.model.sets:
            index = self.model.sets.index(self.editing) + 1
        else:
            self.editing = None
            index = 0
        self.set_list.selection_clear(0, "end")
        self.set_list.selection_set(index)

    def _on_set_selected(self, _event: object) -> None:
        selection = self.set_list.curselection()
        if not selection:
            return
        index = selection[0]
        self.editing = None if index == 0 else self.model.sets[index - 1]
        self._invalid.clear()
        self._load_param_values()
        self._refresh_editing_label()
        self._show_param_problems()

    # -- lifecycle ------------------------------------------------------------

    def onSelect(self) -> None:
        self.refresh(self.model.state)

    def refresh(self, state: MachineState) -> None:
        """Redraw from the model. Called on tab switch and on every state change."""
        busy = state in (MachineState.PREPARING, MachineState.RUNNING)

        self._rebuild_set_list()
        self._load_param_values()
        self._refresh_editing_label()
        self._paint_checkboxes()

        for button in self.check_buttons:
            button["state"] = "disabled" if busy else "normal"
        for button in (
            self.create_button,
            self.select_all_button,
            self.clear_selection_button,
            self.delete_button,
            self.reset_button,
            self.copy_button,
        ):
            button["state"] = "disabled" if busy else "normal"

        entry_state = "disabled" if busy else "normal"
        for entry in self.param_entries.values():
            entry["state"] = entry_state

        if busy:
            self.param_message.configure(
                text="The machine is busy. Stop it before changing motors or parameters."
            )
        else:
            self._show_param_problems()

    # -- drag selection -------------------------------------------------------

    def _drag_start(self, _event: object) -> None:
        self._drag_origin = (
            self.root.winfo_pointerx(),
            self.root.winfo_pointery(),
        )

    def _drag_end(self, _event: object) -> None:
        """Tick every free motor whose checkbox centre falls inside the drag."""
        if self._drag_origin is None:
            return
        start_x, start_y = self._drag_origin
        self._drag_origin = None
        end_x, end_y = self.root.winfo_pointerx(), self.root.winfo_pointery()

        if abs(end_x - start_x) < 5 and abs(end_y - start_y) < 5:
            return  # a click, not a drag

        left, right = sorted((start_x, end_x))
        top, bottom = sorted((start_y, end_y))

        changed = False
        for axis in range(30):
            if self.model.axis_owner(axis) is not None:
                continue
            button = self.check_buttons[axis]
            centre_x = button.winfo_rootx() + button.winfo_width() // 2
            centre_y = button.winfo_rooty() + button.winfo_height() // 2
            if left < centre_x < right and top < centre_y < bottom:
                self.check_vars[axis].set(1)
                self.model.toggle(axis, True)
                changed = True

        if changed:
            self._paint_checkboxes()
            self._refresh_editing_label()
