"""Control Home: prepare the machine, run it, stop it.

Which buttons are usable is decided in one place, :meth:`ControlHome.refresh`,
from the machine state.  Previously button states were poked from a dozen
places -- including from inside worker threads -- which is how the application
ended up with a "Start Curve" button that could only be reached by first running
a single stroke, and a "Prepare" button that stayed disabled forever if a write
failed.
"""

from __future__ import annotations

import os
from logging import Logger, getLogger
from tkinter import (
    Canvas,
    Checkbutton,
    IntVar,
    Radiobutton,
    StringVar,
    messagebox,
    ttk,
)
from typing import Dict, List, Optional

from Model import MachineState, Model, RunMode
from modules.logging.log_utils import LOGGER_NAME
from modules.tooltip import Tooltip

#: Fill colours cycled through so each motor set is distinguishable at a glance.
SET_COLOURS: List[str] = [
    "#7ed957",  # green
    "#4fc3f7",  # blue
    "#ffb74d",  # orange
    "#ba68c8",  # purple
    "#f06292",  # pink
    "#fff176",  # yellow
]

IDLE_COLOUR = "white"


class ControlHome:
    """The Control Home tab."""

    logger: Logger = getLogger(LOGGER_NAME)

    def __init__(self, root: ttk.Notebook, model: Model, view) -> None:
        self.tab = ttk.Frame(root)
        self.model = model
        self.view = view

        self.title_frame = ttk.Frame(self.tab, padding=(25, 20, 25, 0))
        self.content_frame = ttk.Frame(self.tab, padding=25)
        self.title_frame.grid(row=0, column=0, sticky="w")
        self.content_frame.grid(row=1, column=0, sticky="nsew")

        ttk.Label(
            self.title_frame, text="Control Home", style="Heading.TLabel"
        ).grid(row=0, column=0, sticky="w")

        banner = ttk.Frame(self.title_frame)
        banner.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.connection_label = ttk.Label(banner, text="")
        self.connection_label.grid(row=0, column=0, sticky="w")

        # Shown only when the machine is not connected. The launcher no longer
        # opens Studio 5000 and waits for a keypress, so recovery lives here
        # instead: press Reconnect, or open Studio 5000 if the controller needs
        # to be put back in Run.
        self.reconnect_button = ttk.Button(
            banner, text="Reconnect", width=12, command=self.reconnect
        )
        self.studio_button = ttk.Button(
            banner, text="Open Studio 5000", width=18, command=self.open_studio
        )
        self.reconnect_button.grid(row=0, column=1, padx=(16, 6))
        self.studio_button.grid(row=0, column=2)

        self._build_motor_map()
        self._build_status()
        self._build_controls()
        self._build_analytics()

        self.refresh(model.state)
        root.add(self.tab, text=" Control Home")

    # -- construction ---------------------------------------------------------

    def _build_motor_map(self) -> None:
        """The three-by-ten picture of the piston array."""
        frame = ttk.Frame(self.content_frame)
        frame.grid(row=0, column=0, sticky="w")

        ttk.Label(frame, text="Motor layout").grid(row=0, column=0, sticky="w")
        self.circles = Canvas(
            frame, width=1040, height=170, background="#777A7A", highlightthickness=0
        )
        self.circles.grid(row=1, column=0, pady=(6, 0))

        self.motor_circles: Dict[int, int] = {}
        self.motor_labels: Dict[int, int] = {}
        for column in range(10):
            for row in range(3):
                axis = column * 3 + row
                x = 55 + column * 100
                y = 32 + row * 50
                self.motor_circles[axis] = self.circles.create_oval(
                    x - 20, y - 20, x + 20, y + 20, fill=IDLE_COLOUR, outline="#555"
                )
                self.motor_labels[axis] = self.circles.create_text(
                    x, y, text=str(axis), fill="#333"
                )

        self.legend = ttk.Label(frame, text="No motors selected.")
        self.legend.grid(row=2, column=0, sticky="w", pady=(8, 0))

    def _build_status(self) -> None:
        frame = ttk.Frame(self.content_frame)
        frame.grid(row=1, column=0, sticky="ew", pady=(20, 0))

        self.step_label = ttk.Label(frame, text="", style="Step.TLabel")
        self.step_label.grid(row=0, column=0, sticky="w")

        self.statusvar = StringVar(
            value="Visit the Define Motors tab to choose which motors to run."
        )
        self.status_label = ttk.Label(
            frame, textvariable=self.statusvar, wraplength=980, justify="left"
        )
        self.status_label.grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.progress_frame = ttk.Frame(frame)
        self.progress_label = ttk.Label(self.progress_frame, text="")
        self.progress_label.grid(row=0, column=0, sticky="w")
        self.progress_bar = ttk.Progressbar(
            self.progress_frame, length=460, mode="determinate", maximum=100
        )
        self.progress_bar.grid(row=1, column=0, sticky="w", pady=(2, 0))
        # Gridded and removed on demand rather than created and destroyed, so a
        # run that ends unexpectedly cannot leave a stray widget behind.
        self.progress_frame.grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.progress_frame.grid_remove()

    def _build_controls(self) -> None:
        frame = ttk.Frame(self.content_frame)
        frame.grid(row=2, column=0, sticky="w", pady=(20, 0))

        mode_frame = ttk.Frame(frame)
        mode_frame.grid(row=0, column=0, sticky="w", pady=(0, 10))
        ttk.Label(mode_frame, text="Run mode:").grid(row=0, column=0, padx=(0, 10))
        self.run_mode = IntVar(value=1)
        self.run_single = Radiobutton(
            mode_frame, text="One stroke", variable=self.run_mode, value=1
        )
        self.run_continuous = Radiobutton(
            mode_frame, text="Continuous", variable=self.run_mode, value=2
        )
        self.run_single.grid(row=0, column=1, sticky="w")
        self.run_continuous.grid(row=0, column=2, sticky="w", padx=(10, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, sticky="w")

        self.prepare_button = ttk.Button(
            buttons, text="1. Prepare Motor(s)", width=22, command=self.prepare
        )
        self.start_button = ttk.Button(
            buttons, text="2. Start Motor(s)", width=22, command=self.start
        )
        self.curve_button = ttk.Button(
            buttons, text="2. Start Curve", width=22, command=self.start_curve
        )
        self.stop_button = ttk.Button(
            buttons, text="Stop Motor(s)", width=22, command=self.stop
        )
        self.reset_button = ttk.Button(
            buttons, text="Off and Reset", width=22, command=self.reset
        )

        for column, button in enumerate(
            (
                self.prepare_button,
                self.start_button,
                self.curve_button,
                self.stop_button,
                self.reset_button,
            )
        ):
            button.grid(row=0, column=column, padx=(0, 10))

        Tooltip(
            self.prepare_button,
            "Writes your parameters to the machine and homes every piston.\n"
            "Required after changing motors or parameters.",
        )
        Tooltip(
            self.start_button,
            "Runs the motors using the run mode selected above.",
        )
        Tooltip(
            self.curve_button,
            "Runs the stored curve given by the Curve ID parameter.\n"
            "Available once the motors are prepared.",
        )
        Tooltip(
            self.stop_button,
            "Drops every run bit immediately. Works while the machine is busy.",
        )
        Tooltip(
            self.reset_button,
            "Turns the motors off, clears faults and returns the application\n"
            "to its just-launched state.",
        )

    def _build_analytics(self) -> None:
        frame = ttk.Frame(self.content_frame)
        frame.grid(row=3, column=0, sticky="w", pady=(24, 0))

        self.analytics_on = IntVar(value=0)
        self.analytics_checkbox = Checkbutton(
            frame,
            text="Record analytics",
            variable=self.analytics_on,
            command=self.toggle_analytics,
        )
        self.analytics_checkbox.grid(row=0, column=0, sticky="w")

        ttk.Button(frame, text="What is this?", command=self.show_analytics_info).grid(
            row=0, column=1, padx=(10, 30)
        )

        # The interval and duration boxes are always present and simply become
        # editable. The old code created and destroyed them on every tick, and
        # unticking before ticking raised AttributeError.
        self.interval_var = StringVar(value="0.25")
        self.duration_var = StringVar(value="10.0")

        ttk.Label(frame, text="Interval (s)").grid(row=0, column=2, padx=(0, 6))
        self.interval_entry = ttk.Entry(frame, textvariable=self.interval_var, width=8)
        self.interval_entry.grid(row=0, column=3, padx=(0, 20))

        ttk.Label(frame, text="Duration (s)").grid(row=0, column=4, padx=(0, 6))
        self.duration_entry = ttk.Entry(frame, textvariable=self.duration_var, width=8)
        self.duration_entry.grid(row=0, column=5)

        self.interval_var.trace_add("write", lambda *_: self.read_analytics_settings())
        self.duration_var.trace_add("write", lambda *_: self.read_analytics_settings())
        self._set_analytics_enabled(False)

    # -- display --------------------------------------------------------------

    def onSelect(self) -> None:
        self.refresh(self.model.state)

    def set_status(self, message: str) -> None:
        self.statusvar.set(message)

    def refresh(self, state: MachineState) -> None:
        """Set every button from the machine state. The single source of truth."""
        self.connection_label.configure(text=self._connection_text())

        # The recovery buttons only clutter the screen when connected.
        offline = not self.model.is_live and getattr(
            self.model, "_connection_attempted", True
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
            MachineState.PREPARING: ("stop",),
            MachineState.HOMED: ("start", "curve", "stop", "reset"),
            MachineState.RUNNING: ("stop",),
        }[state]

        for key, button in (
            ("prepare", self.prepare_button),
            ("start", self.start_button),
            ("curve", self.curve_button),
            ("stop", self.stop_button),
            ("reset", self.reset_button),
        ):
            button["state"] = "normal" if key in enabled else "disabled"

        if state is MachineState.IDLE:
            self.reset_button["state"] = "normal" if self.model.sets else "disabled"

        self.step_label.configure(text=self._step_text(state))
        self.draw_motor_map()

    def _connection_text(self) -> str:
        """The banner under the title: whether this is the real machine."""
        if not getattr(self.model, "_connection_attempted", True):
            return "Looking for the PLC at {0}...".format(self.model.ip_address)
        if self.model.is_live:
            return "Connected to PLC at {0}".format(self.model.ip_address)
        return "SIMULATION - no PLC at {0}. Nothing will move.".format(
            self.model.ip_address
        )

    def _step_text(self, state: MachineState) -> str:
        return {
            MachineState.IDLE: "Step 1 of 3  -  choose motors on the Define Motors tab",
            MachineState.READY: "Step 2 of 3  -  press Prepare Motor(s)",
            MachineState.PREPARING: "Preparing  -  please wait",
            MachineState.HOMED: "Step 3 of 3  -  press Start Motor(s) or Start Curve",
            MachineState.RUNNING: "Running  -  press Stop Motor(s) when finished",
        }[state]

    def draw_motor_map(self) -> None:
        """Colour each piston by the set it belongs to."""
        for axis in range(len(self.motor_circles)):
            self.circles.itemconfig(self.motor_circles[axis], fill=IDLE_COLOUR)

        legend_parts = []
        for index, motor_set in enumerate(self.model.sets):
            colour = SET_COLOURS[index % len(SET_COLOURS)]
            for axis in motor_set.axes:
                self.circles.itemconfig(self.motor_circles[axis], fill=colour)
            legend_parts.append("{0} ({1} motors)".format(motor_set.name, len(motor_set)))

        self.legend.configure(
            text="   |   ".join(legend_parts) if legend_parts else "No motors selected."
        )

    # -- progress -------------------------------------------------------------

    def show_progress(self, fraction: float, label: str) -> None:
        self.progress_frame.grid()
        self.progress_label.configure(
            text="{0}: {1:.0f}%".format(label, min(max(fraction, 0.0), 1.0) * 100)
        )
        self.progress_bar["value"] = min(max(fraction, 0.0), 1.0) * 100

    def finish_progress(self, artifact: Optional[str]) -> None:
        self.progress_frame.grid_remove()
        self.progress_bar["value"] = 0
        if artifact and messagebox.askyesno(
            "Recording finished", "Open the analytics file?", parent=self.tab
        ):
            try:
                os.startfile(artifact)  # noqa: S606 - opening the app's own output
            except OSError as exc:
                self.logger.error("Could not open %s: %s", artifact, exc)

    # -- commands -------------------------------------------------------------

    def prepare(self) -> None:
        self.set_status("Preparing motors...")
        self.model.prepare()

    def start(self) -> None:
        self.read_analytics_settings()
        mode = RunMode.SINGLE if self.run_mode.get() == 1 else RunMode.CONTINUOUS
        self.model.start(mode)

    def start_curve(self) -> None:
        self.read_analytics_settings()
        self.model.start(RunMode.CURVE)

    def stop(self) -> None:
        self.model.stop()

    def reconnect(self) -> None:
        self.set_status("Looking for the PLC...")
        self.model.reconnect()

    def open_studio(self) -> None:
        """Open the PLC project, for when the controller needs putting in Run."""
        from app import external

        if not external.open_studio_5000():
            messagebox.showerror(
                "Studio 5000 project not found",
                "Expected the project at:\n{0}\n\n"
                "Open it yourself, go online, and put the controller in "
                "Rem Run. Then press Reconnect.".format(external.STUDIO_PROJECT),
                parent=self.tab,
            )
            return
        messagebox.showinfo(
            "Studio 5000",
            "Studio 5000 is opening.\n\n"
            "Go Online, then put the controller in Rem Run.\n"
            "Come back here and press Reconnect.\n\n"
            "You can leave Studio 5000 closed once the controller is in Run - "
            "this application does not need it.",
            parent=self.tab,
        )

    def reset(self) -> None:
        if messagebox.askyesno(
            "Off and reset",
            "Turn the motors off, clear every motor set and return the "
            "application to its starting state?",
            parent=self.tab,
        ):
            self.model.reset()
            self.analytics_on.set(0)
            self.toggle_analytics()

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
        """Take the interval and duration from the boxes, keeping the last good
        value if what is typed is not a number."""
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
            "analytics/<date>.txt. Runs on the same day are appended to the "
            "same file and separated by a header.\n\n"
            "Sampling happens during continuous runs and curve runs.\n\n"
            "Test your parameters without analytics first, so you know they "
            "will not fault the machine, then run again with analytics on.",
            parent=self.tab,
        )
