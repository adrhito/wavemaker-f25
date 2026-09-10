"""The main window.

Two things live outside the tabs, because they matter no matter which tab you
are on: the machine state and the stop control. Previously Stop existed twice,
once on Control Home and once on Define Motors, and on the other two tabs there
was no way to stop the machine at all without switching tab first.

The view is also the model's :class:`~Model.UiBridge`. Model commands run on a
worker thread and report back through this class, which hands every update to
``root.after`` so widgets are only ever touched from the main thread.
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, Optional

from Model import MachineState, Model
from control_home.ControlHome import ControlHome
from define_motors.DefineMotors import DefineMotors
from feedback.Feedback import Feedback
from preset_options.PresetOptions import PresetOptions
from style import STATE_COLOURS, style_GUI

#: How often the main thread drains the callback queue, in milliseconds.
PUMP_INTERVAL_MS = 40

#: Smallest window that still lays out properly. The lab display size is not
#: known, so everything is built to work at this size and scale up.
MIN_WIDTH = 1180
MIN_HEIGHT = 700


class View:
    """Builds the window and routes model callbacks onto the Tk thread."""

    def __init__(self, model: Model) -> None:
        self.model = model
        self._queue: queue.Queue = queue.Queue()
        self._closing = False

        self.root = tk.Tk()
        self.root.configure(bg="black")
        self.root.title("Wavemaker System Control")
        self.root.minsize(MIN_WIDTH, MIN_HEIGHT)
        self._size_to_screen()

        style_GUI()

        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.tabControl = ttk.Notebook(self.root)
        self.tabControl.grid(row=0, column=0, sticky="nsew")

        self.control_home = ControlHome(self.tabControl, model, self)
        self.define_motors = DefineMotors(self.tabControl, model, self)
        self.preset_options = PresetOptions(self.tabControl, model, self)
        self.feedback = Feedback(self.tabControl, model)

        self._build_status_bar()

        self.tabControl.bind("<<NotebookTabChanged>>", self._tab_changed)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Escape>", lambda _e: self.stop())

        model.register_bridge(self)
        self.root.after(PUMP_INTERVAL_MS, self._pump)

    def _size_to_screen(self) -> None:
        """Open at a sensible size for whatever display this is."""
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(max(int(screen_w * 0.88), MIN_WIDTH), 1680)
        height = min(max(int(screen_h * 0.86), MIN_HEIGHT), 980)
        x = max((screen_w - width) // 2, 0)
        y = max((screen_h - height) // 3, 0)
        self.root.geometry("{0}x{1}+{2}+{3}".format(width, height, x, y))

    def run(self) -> None:
        self.model.startup()
        self.model.start_monitoring()
        self.root.mainloop()

    # -- status bar -----------------------------------------------------------

    def _build_status_bar(self) -> None:
        bar = tk.Frame(self.root, bg="#1b2129", height=52)
        bar.grid(row=1, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.columnconfigure(2, weight=1)

        self.state_chip = tk.Label(
            bar, text="", bg="#1b2129", fg="#0d1117",
            font=("Segoe UI", 9, "bold"), padx=12, pady=4,
        )
        self.state_chip.grid(row=0, column=0, padx=(16, 12), pady=11)

        self.status_var = tk.StringVar(value="Starting...")
        tk.Label(
            bar, textvariable=self.status_var, bg="#1b2129", fg="#dfe6ec",
            font=("Segoe UI", 9), anchor="w",
        ).grid(row=0, column=2, sticky="ew")

        self.progress = ttk.Progressbar(bar, length=190, maximum=100)
        self.progress.grid(row=0, column=3, padx=(12, 12))
        self.progress.grid_remove()

        # Stop is a plain tk.Button so it can actually be red; ttk on Windows
        # ignores background on buttons.
        self.stop_button = tk.Button(
            bar, text="STOP", command=self.stop,
            bg="#c62828", fg="white", activebackground="#e53935",
            activeforeground="white", font=("Segoe UI", 10, "bold"),
            relief="flat", width=12, cursor="hand2",
            disabledforeground="#8d9aa6",
        )
        self.stop_button.grid(row=0, column=4, padx=(0, 16), pady=8)
        self.stop_button.configure(state="disabled", bg="#3a2222")

    def set_status_text(self, message: str) -> None:
        self.status_var.set(message)

    def show_progress(self, fraction: float, label: str) -> None:
        self.progress.grid()
        self.progress["value"] = min(max(fraction, 0.0), 1.0) * 100

    def hide_progress(self) -> None:
        self.progress.grid_remove()
        self.progress["value"] = 0

    def stop(self) -> None:
        self.model.stop()

    def _refresh_status_bar(self, state: MachineState) -> None:
        label, colour = STATE_COLOURS[state]
        self.state_chip.configure(text=label, bg=colour)

        # Stop is live whenever the machine could be doing something.
        can_stop = state in (MachineState.RUNNING, MachineState.PREPARING)
        self.stop_button.configure(
            state="normal" if can_stop else "disabled",
            bg="#c62828" if can_stop else "#3a2222",
        )

    # -- thread marshalling ---------------------------------------------------

    def post(self, callback) -> None:
        """Queue ``callback`` to run on the main thread."""
        self._queue.put(callback)

    def _pump(self) -> None:
        while True:
            try:
                callback = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:  # pragma: no cover - a bad callback must not stop
                self.model.LOGGER.exception("Error updating the display")
        if not self._closing:
            self.root.after(PUMP_INTERVAL_MS, self._pump)

    # -- UiBridge -------------------------------------------------------------

    def status(self, message: str) -> None:
        self.post(lambda: self.set_status_text(message))

    def state_changed(self, state: MachineState) -> None:
        def apply() -> None:
            self._refresh_status_bar(state)
            self.control_home.refresh(state)
            self.define_motors.refresh(state)
            self.preset_options.refresh(state)

        self.post(apply)

    def progress(self, fraction: float, label: str) -> None:
        self.post(lambda: self.show_progress(fraction, label))

    def progress_done(self, artifact: Optional[str]) -> None:
        self.post(lambda: self.control_home.finish_progress(artifact))

    def problem(self, title: str, message: str) -> None:
        self.post(lambda: messagebox.showerror(title, message, parent=self.root))

    def positions(self, readings: Dict[int, float]) -> None:
        self.post(lambda: self.control_home.show_positions(readings))

    # -- window events --------------------------------------------------------

    def _tab_changed(self, _event: object) -> None:
        index = self.tabControl.index(self.tabControl.select())
        tabs = (
            self.control_home, self.define_motors,
            self.preset_options, self.feedback,
        )
        if 0 <= index < len(tabs):
            tabs[index].onSelect()

    def _on_close(self) -> None:
        """Stop the machine before the window disappears."""
        if self.model.state is MachineState.RUNNING:
            if not messagebox.askyesno(
                "Motors are running",
                "The motors are still running.\n\n"
                "Closing will stop them and turn them off. Continue?",
                parent=self.root,
            ):
                return

        self._closing = True
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        self.model.shutdown()
        self.root.destroy()
