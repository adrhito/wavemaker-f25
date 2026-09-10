"""Control Home: watch the array, prepare, run, stop.

The array is drawn as the tank it is, and while the machine runs the pistons
show where they actually are, read from the drives four times a second. A stuck
or faulted piston is then visible at a glance instead of being something you
work out afterwards from the analytics file.

Which buttons are usable is decided in one place, :meth:`refresh`, from the
machine state.
"""

from __future__ import annotations

import os
from logging import Logger, getLogger
from tkinter import Checkbutton, IntVar, StringVar, messagebox, ttk
from typing import Dict, Optional

from Model import MachineState, Model, RunMode
from modules.logging.log_utils import LOGGER_NAME
from modules.tank_view import TankView, describe_place
from modules.tooltip import Tooltip


class ControlHome:
    """The Control Home tab."""

    logger: Logger = getLogger(LOGGER_NAME)

    def __init__(self, root: ttk.Notebook, model: Model, view) -> None:
        self.tab = ttk.Frame(root)
        self.model = model
        self.view = view

        self.tab.columnconfigure(0, weight=1)
        self.tab.rowconfigure(1, weight=1)

        self._build_header()

        body = ttk.Frame(self.tab, padding=(22, 12, 22, 16))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        self._build_tank(body)
        self._build_controls(body)
        self._build_analytics(body)

        self.refresh(model.state)
        root.add(self.tab, text=" Control Home")

    # -- construction ---------------------------------------------------------

    def _build_header(self) -> None:
        header = ttk.Frame(self.tab, padding=(22, 16, 22, 0))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="Control Home", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w"
        )

        banner = ttk.Frame(header)
        banner.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.connection_label = ttk.Label(banner, text="", style="Dim.TLabel")
        self.connection_label.grid(row=0, column=0, sticky="w")

        # Only shown while the machine is unreachable.
        self.reconnect_button = ttk.Button(
            banner, text="Reconnect", width=12, command=self.reconnect
        )
        self.studio_button = ttk.Button(
            banner, text="Open Studio 5000", width=18, command=self.open_studio
        )
        self.reconnect_button.grid(row=0, column=1, padx=(16, 6))
        self.studio_button.grid(row=0, column=2)

    def _build_tank(self, parent) -> None:
        self.tank = TankView(
            parent, on_hover=self._on_hover, height=230, interactive=False
        )
        self.tank.grid(row=0, column=0, sticky="ew")

        under = ttk.Frame(parent)
        under.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        under.columnconfigure(1, weight=1)

        self.legend = ttk.Label(under, text="", style="Dim.TLabel")
        self.legend.grid(row=0, column=0, sticky="w")
        self.hover_label = ttk.Label(under, text=" ", style="Dim.TLabel")
        self.hover_label.grid(row=0, column=1, sticky="e")

    def _build_controls(self, parent) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=2, column=0, sticky="w", pady=(20, 0))

        mode = ttk.Frame(frame)
        mode.grid(row=0, column=0, sticky="w", pady=(0, 10))
        ttk.Label(mode, text="Run mode").grid(row=0, column=0, padx=(0, 12))
        self.run_mode = IntVar(value=1)
        ttk.Radiobutton(
            mode, text="One stroke", variable=self.run_mode, value=1
        ).grid(row=0, column=1, padx=(0, 12))
        ttk.Radiobutton(
            mode, text="Continuous", variable=self.run_mode, value=2
        ).grid(row=0, column=2)

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, sticky="w")

        self.prepare_button = ttk.Button(
            buttons, text="1.  Prepare Motors", width=20, command=self.prepare
        )
        self.start_button = ttk.Button(
            buttons, text="2.  Start Motors", width=20, command=self.start
        )
        self.curve_button = ttk.Button(
            buttons, text="2.  Start Curve", width=18, command=self.start_curve
        )
        self.reset_button = ttk.Button(
            buttons, text="Off and Reset", width=16, command=self.reset
        )
        for column, button in enumerate(
            (self.prepare_button, self.start_button, self.curve_button,
             self.reset_button)
        ):
            button.grid(row=0, column=column, padx=(0, 8))

        Tooltip(
            self.prepare_button,
            "Writes your parameters to the machine and homes every piston.\n"
            "Needed after changing motors or parameters.",
        )
        Tooltip(
            self.curve_button,
            "Runs the stored curve given by the Curve ID parameter.",
        )
        Tooltip(
            self.reset_button,
            "Turns the motors off, clears faults and empties every set.",
        )

    def _build_analytics(self, parent) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=3, column=0, sticky="w", pady=(22, 0))

        self.analytics_on = IntVar(value=0)
        self.analytics_checkbox = Checkbutton(
            frame,
            text="Record analytics",
            variable=self.analytics_on,
            command=self.toggle_analytics,
            bg="black",
            fg="#dfe6ec",
            selectcolor="#2a3542",
            activebackground="black",
            activeforeground="#ffffff",
            highlightthickness=0,
            bd=0,
        )
        self.analytics_checkbox.grid(row=0, column=0, sticky="w")

        ttk.Button(frame, text="?", width=3, command=self.show_analytics_info).grid(
            row=0, column=1, padx=(8, 24)
        )

        self.interval_var = StringVar(value="0.25")
        self.duration_var = StringVar(value="10.0")
        ttk.Label(frame, text="Interval (s)").grid(row=0, column=2, padx=(0, 6))
        self.interval_entry = ttk.Entry(frame, textvariable=self.interval_var, width=7)
        self.interval_entry.grid(row=0, column=3, padx=(0, 18))
        ttk.Label(frame, text="Duration (s)").grid(row=0, column=4, padx=(0, 6))
        self.duration_entry = ttk.Entry(frame, textvariable=self.duration_var, width=7)
        self.duration_entry.grid(row=0, column=5)

        self.interval_var.trace_add("write", lambda *_a: self.read_analytics_settings())
        self.duration_var.trace_add("write", lambda *_a: self.read_analytics_settings())
        self._set_analytics_enabled(False)

    # -- display --------------------------------------------------------------

    def onSelect(self) -> None:
        self.refresh(self.model.state)

    def set_status(self, message: str) -> None:
        """Kept so the model's status messages have somewhere to go; the text
        itself is shown in the window's status bar."""
        self.view.set_status_text(message)

    def _connection_text(self) -> str:
        """The banner: which of three situations this session is in.

        Simulation on purpose and simulation because the machine could not be
        found look the same from the inside but need different words, and only
        one of them has anything the operator can do about it.
        """
        if not getattr(self.model, "_connection_attempted", True):
            return "Looking for the PLC at {0}...".format(self.model.ip_address)
        if self.model.is_live:
            return "Connected to PLC at {0}".format(self.model.ip_address)
        if getattr(self.model, "_simulate", False):
            return (
                "SIMULATION MODE - started with --simulate, so the machine is "
                "not used. Nothing will move. Restart without that option to "
                "run the wavemaker."
            )
        return (
            "NO CONNECTION - the PLC at {0} did not answer, so nothing will "
            "move. Check the controller is powered and in Run, then press "
            "Reconnect.".format(self.model.ip_address)
        )

    def refresh(self, state: MachineState) -> None:
        self.connection_label.configure(text=self._connection_text())

        # Reconnect is only shown when it can actually achieve something. A
        # session started with --simulate deliberately has no machine, so
        # offering the button there is a dead end.
        deliberate = getattr(self.model, "_simulate", False)
        offline = (
            not self.model.is_live
            and getattr(self.model, "_connection_attempted", True)
            and not deliberate
        )
        busy = state in (MachineState.PREPARING, MachineState.RUNNING)
        for button in (self.reconnect_button, self.studio_button):
            if offline and not busy:
                button.grid()
            else:
                button.grid_remove()

        enabled = {
            MachineState.IDLE: (),
            MachineState.READY: ("prepare", "reset"),
            MachineState.PREPARING: (),
            MachineState.HOMED: ("start", "curve", "reset"),
            MachineState.RUNNING: (),
        }[state]
        for key, button in (
            ("prepare", self.prepare_button),
            ("start", self.start_button),
            ("curve", self.curve_button),
            ("reset", self.reset_button),
        ):
            button["state"] = "normal" if key in enabled else "disabled"
        if state is MachineState.IDLE:
            self.reset_button["state"] = "normal" if self.model.sets else "disabled"

        self._set_analytics_enabled(bool(self.analytics_on.get()) and not busy)

        self.tank.show_sets(self.model.sets)
        self.tank.show_strokes(
            dict(
                (m.axis, (m.write_params["Position 1"], m.write_params["Position 2"]))
                for m in self.model.all_motors
            )
        )
        if state is not MachineState.RUNNING:
            self.tank.clear_positions()

        parts = [
            "{0}: {1} piston(s)".format(s.name, len(s)) for s in self.model.sets
        ]
        self.legend.configure(
            text="   |   ".join(parts) if parts else "No pistons selected yet."
        )

    def show_positions(self, readings: Dict[int, float]) -> None:
        self.tank.show_positions(readings)
        if self.model.unreadable_axes:
            self.tank.show_faults(self.model.unreadable_axes)

    def _on_hover(self, axis: Optional[int]) -> None:
        if axis is None:
            self.hover_label.configure(text=" ")
            return
        owner = self.model.axis_owner(axis)
        if owner is None:
            self.hover_label.configure(
                text="Motor {0}  -  {1}  -  not in a set".format(
                    axis, describe_place(axis)
                )
            )
            return
        position = self.tank.positions.get(axis)
        text = "Motor {0}  -  {1}  -  {2}".format(
            axis, describe_place(axis), owner.name
        )
        if position is not None:
            text += "  -  at {0:.0f} mm".format(position)
        self.hover_label.configure(text=text)

    # -- commands -------------------------------------------------------------

    def prepare(self) -> None:
        self.model.prepare()

    def start(self) -> None:
        self.read_analytics_settings()
        self.model.start(
            RunMode.SINGLE if self.run_mode.get() == 1 else RunMode.CONTINUOUS
        )

    def start_curve(self) -> None:
        self.read_analytics_settings()
        self.model.start(RunMode.CURVE)

    def reset(self) -> None:
        if messagebox.askyesno(
            "Off and reset",
            "Turn the motors off, clear every set and return the application "
            "to its starting state?",
            parent=self.tab,
        ):
            self.model.reset()
            self.analytics_on.set(0)
            self.toggle_analytics()

    def reconnect(self) -> None:
        self.view.set_status_text("Looking for the PLC...")
        self.model.reconnect()

    def open_studio(self) -> None:
        from app import external

        if not external.open_studio_5000():
            messagebox.showerror(
                "Studio 5000 project not found",
                "Expected the project at:\n{0}\n\n"
                "Open it yourself, go online and put the controller in Rem Run, "
                "then press Reconnect.".format(external.STUDIO_PROJECT),
                parent=self.tab,
            )
            return
        messagebox.showinfo(
            "Studio 5000",
            "Studio 5000 is opening.\n\n"
            "Go Online, then put the controller in Rem Run.\n"
            "Come back here and press Reconnect.\n\n"
            "You can close Studio 5000 again afterwards - this application does "
            "not need it.",
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

    # -- analytics ------------------------------------------------------------

    def _set_analytics_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.interval_entry["state"] = state
        self.duration_entry["state"] = state

    def toggle_analytics(self) -> None:
        self.model.record_analytics = bool(self.analytics_on.get())
        self._set_analytics_enabled(self.model.record_analytics)
        if self.model.record_analytics:
            self.read_analytics_settings()

    def read_analytics_settings(self) -> None:
        self.model.analytics_interval = self._positive_float(
            self.interval_var.get(), self.model.analytics_interval, "interval"
        )
        self.model.analytics_duration = self._positive_float(
            self.duration_var.get(), self.model.analytics_duration, "duration"
        )

    def _positive_float(self, text: str, fallback: float, what: str) -> float:
        try:
            value = float(text)
        except ValueError:
            return fallback
        if value <= 0:
            self.logger.warning("Analytics %s must be greater than zero.", what)
            return fallback
        return value

    def show_analytics_info(self) -> None:
        messagebox.showinfo(
            "Record analytics",
            "With this ticked, the application samples each piston's demanded "
            "and actual position while it runs, and writes a table to "
            "analytics/<date>.txt. Runs on the same day go to the same file, "
            "separated by a header.\n\n"
            "Sampling happens on continuous runs and curve runs.\n\n"
            "The live view above always shows actual positions while running; "
            "this is for keeping a record of them.",
            parent=self.tab,
        )
