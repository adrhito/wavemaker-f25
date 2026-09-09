"""Preset Options: load saved parameters onto motor sets, and save new ones."""

from __future__ import annotations

import os
from logging import Logger, getLogger
from tkinter import IntVar, StringVar, filedialog, messagebox, simpledialog, ttk
from typing import Dict, List, Optional

from app import params, paths
from Model import MachineState, Model
from modules.logging.log_utils import LOGGER_NAME
from modules.tooltip import Tooltip
from preset_options.Preset import Preset
from preset_options.PresetProcessor import PresetError, PresetProcessor


class PresetOptions:
    """The Preset Options tab."""

    logger: Logger = getLogger(LOGGER_NAME)

    def __init__(self, root: ttk.Notebook, model: Model, view) -> None:
        self.tab = ttk.Frame(root)
        self.model = model
        self.view = view
        self.processor = PresetProcessor(model)
        self.preset: Optional[Preset] = None

        self.set_vars: List[IntVar] = []
        self.set_boxes: List[ttk.Checkbutton] = []

        self.title_frame = ttk.Frame(self.tab, padding=(25, 20, 25, 0))
        self.content_frame = ttk.Frame(self.tab, padding=25)
        self.title_frame.grid(row=0, column=0, sticky="w")
        self.content_frame.grid(row=1, column=0, sticky="nsew")

        ttk.Label(
            self.title_frame, text="Preset Options", style="Heading.TLabel"
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            self.title_frame,
            text="Load saved parameters onto one or more sets, or save the sets "
            "you have built.",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        self._build_controls()
        self._build_preview()
        self._build_set_picker()

        self.refresh(model.state)
        root.add(self.tab, text="Preset Options")

    # -- construction ---------------------------------------------------------

    def _build_controls(self) -> None:
        frame = ttk.Frame(self.content_frame)
        frame.grid(row=0, column=0, sticky="nw", padx=(0, 40))

        self.select_button = ttk.Button(
            frame, text="Select Preset...", width=22, command=self.browse
        )
        self.apply_button = ttk.Button(
            frame, text="Apply to Selected Sets", width=22, command=self.apply_preset
        )
        self.create_button = ttk.Button(
            frame, text="Save Current Sets...", width=22, command=self.save_preset
        )

        self.select_button.grid(row=0, column=0, pady=(0, 10))
        self.apply_button.grid(row=1, column=0, pady=(0, 10))
        self.create_button.grid(row=2, column=0)

        Tooltip(
            self.create_button,
            "Writes every motor set's parameters to a new CSV in the Presets folder.",
        )

        self.loaded_var = StringVar(value="No preset loaded.")
        ttk.Label(
            frame, textvariable=self.loaded_var, wraplength=240, justify="left"
        ).grid(row=3, column=0, sticky="w", pady=(16, 0))

    def _build_preview(self) -> None:
        frame = ttk.Frame(self.content_frame)
        frame.grid(row=0, column=1, sticky="nw")

        ttk.Label(frame, text="Values in this preset", style="Step.TLabel").grid(
            row=0, column=0, columnspan=6, sticky="w", pady=(0, 8)
        )

        self.preview_vars: Dict[str, StringVar] = {}
        for index, spec in enumerate(params.PARAMS):
            column, row = divmod(index, 6)
            ttk.Label(frame, text=spec.name).grid(
                row=row + 1, column=column * 2, padx=(0, 8), pady=5, sticky="e"
            )
            var = StringVar(value="-")
            self.preview_vars[spec.name] = var
            ttk.Label(frame, textvariable=var, width=10, anchor="w").grid(
                row=row + 1, column=column * 2 + 1, padx=(0, 24), pady=5, sticky="w"
            )

    def _build_set_picker(self) -> None:
        frame = ttk.Frame(self.content_frame)
        frame.grid(row=1, column=0, columnspan=2, sticky="w", pady=(28, 0))
        self.set_frame = frame

        ttk.Label(
            frame, text="Apply to which sets?", style="Step.TLabel"
        ).grid(row=0, column=0, sticky="w")
        self.set_hint = ttk.Label(frame, text="")
        self.set_hint.grid(row=1, column=0, sticky="w", pady=(4, 6))
        self.set_container = ttk.Frame(frame)
        self.set_container.grid(row=2, column=0, sticky="w")

    # -- display --------------------------------------------------------------

    def onSelect(self) -> None:
        self.refresh(self.model.state)

    def refresh(self, state: MachineState) -> None:
        busy = state in (MachineState.PREPARING, MachineState.RUNNING)
        self._rebuild_set_boxes()

        self.select_button["state"] = "disabled" if busy else "normal"
        self.create_button["state"] = (
            "disabled" if busy or not self.model.sets else "normal"
        )
        self.apply_button["state"] = (
            "normal" if self.preset is not None and self.model.sets and not busy else "disabled"
        )

        if busy:
            self.set_hint.configure(
                text="The machine is busy. Stop it before changing parameters."
            )
        elif not self.model.sets:
            self.set_hint.configure(
                text="No motor sets yet. Create one on the Define Motors tab first."
            )
        else:
            self.set_hint.configure(text="Tick every set this preset should apply to.")

    def _rebuild_set_boxes(self) -> None:
        """Rebuild the set checkboxes, keeping any ticks that still apply."""
        previously_ticked = set(
            index for index, var in enumerate(self.set_vars) if var.get()
        )
        for box in self.set_boxes:
            box.destroy()
        self.set_boxes = []
        self.set_vars = []

        for index, motor_set in enumerate(self.model.sets):
            var = IntVar(value=1 if index in previously_ticked else 0)
            box = ttk.Checkbutton(
                self.set_container,
                text="{0}  (motors {1})".format(
                    motor_set.name, ", ".join(str(a) for a in motor_set.axes)
                ),
                variable=var,
            )
            box.grid(row=index, column=0, sticky="w")
            self.set_vars.append(var)
            self.set_boxes.append(box)

    def _show_preview(self) -> None:
        values = self.preset.preview() if self.preset else {}
        for spec in params.PARAMS:
            self.preview_vars[spec.name].set(
                str(values[spec.name]) if spec.name in values else "-"
            )

    # -- actions --------------------------------------------------------------

    def browse(self) -> None:
        paths.ensure_directories()
        filename = filedialog.askopenfilename(
            initialdir=str(paths.PRESET_DIR),
            title="Select a preset CSV file",
            filetypes=[("Preset CSV", "*.csv"), ("All files", "*.*")],
            parent=self.tab,
        )
        if not filename:
            return  # the operator cancelled

        try:
            self.preset = self.processor.load(filename)
        except PresetError as exc:
            self.preset = None
            self._show_preview()
            self.loaded_var.set("No preset loaded.")
            self.refresh(self.model.state)
            # A preset that will not load now says so, instead of failing
            # silently into a debug log as it used to.
            messagebox.showerror("Could not read preset", str(exc), parent=self.tab)
            return

        self._show_preview()
        self.loaded_var.set("Loaded: {0}".format(os.path.basename(filename)))
        self.refresh(self.model.state)

        message = self.preset.describe()
        if self.preset.warnings:
            message += "\n\n" + "\n".join(self.preset.warnings)
            messagebox.showwarning("Preset loaded with warnings", message, parent=self.tab)
        self.view.status(message)

    def apply_preset(self) -> None:
        if self.preset is None:
            return

        chosen = [
            self.model.sets[index]
            for index, var in enumerate(self.set_vars)
            if var.get()
        ]
        if not chosen:
            messagebox.showinfo(
                "Choose a set",
                "Tick at least one set for the preset to apply to.",
                parent=self.tab,
            )
            return

        applied = 0
        skipped: List[str] = []
        rejected: List[str] = []

        for motor_set in chosen:
            for motor in motor_set:
                values = self.preset.values_for(motor.axis)
                if values is None:
                    skipped.append(
                        "motor {0}: the preset has no values for it".format(motor.axis)
                    )
                    continue
                try:
                    motor.update_params(values)
                except ValueError as exc:
                    rejected.append("motor {0}: {1}".format(motor.axis, exc))
                    continue
                applied += 1

        if applied:
            self.model.mark_unprepared()
            self.view.status(
                "Applied {0} to {1} motor(s) across {2} set(s). "
                "Press Prepare Motor(s) to write them to the machine.".format(
                    self.preset.name, applied, len(chosen)
                )
            )

        if rejected:
            messagebox.showerror(
                "Some values were rejected",
                "These motors kept their previous parameters because the preset "
                "is outside the machine's limits:\n\n" + "\n".join(rejected),
                parent=self.tab,
            )
        elif skipped and not applied:
            messagebox.showwarning(
                "Nothing applied", "\n".join(skipped), parent=self.tab
            )

        self.refresh(self.model.state)

    def save_preset(self) -> None:
        if not self.model.sets:
            messagebox.showinfo(
                "Nothing to save",
                "Create a motor set on the Define Motors tab first.",
                parent=self.tab,
            )
            return

        name = simpledialog.askstring(
            "Save preset", "Name for the new preset:", parent=self.tab
        )
        if not name:
            return

        try:
            target = self.processor.save(name, self.model.sets)
        except PresetError as exc:
            messagebox.showerror("Could not save preset", str(exc), parent=self.tab)
            return

        self.view.status("Saved preset to {0}".format(target))
        messagebox.showinfo(
            "Preset saved", "Saved to:\n{0}".format(target), parent=self.tab
        )
