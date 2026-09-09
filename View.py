"""The main window.

The view also acts as the model's :class:`~Model.UiBridge`.  Model commands run
on a worker thread and report back through this class, which hands every update
to ``root.after`` so the widgets are only ever touched from the main thread.
Worker threads used to call ``self.view.update_msg(...)`` and assign to widget
options directly, which is not safe in Tk and is a good way to get an
intermittent hang with no error message.
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from Model import MachineState, Model
from control_home.ControlHome import ControlHome
from define_motors.DefineMotors import DefineMotors
from feedback.Feedback import Feedback
from preset_options.PresetOptions import PresetOptions
from style import style_GUI

#: How often the main thread drains the callback queue, in milliseconds.
PUMP_INTERVAL_MS = 50


class View:
    """Builds the window and routes model callbacks onto the Tk thread."""

    def __init__(self, model: Model) -> None:
        self.model = model
        self._queue: queue.Queue = queue.Queue()
        self._closing = False

        self.root = tk.Tk()
        self.root.configure(bg="black")
        self.root.geometry("1400x820")
        self.root.minsize(1100, 700)
        self.root.title("Wavemaker System Control")

        style_GUI()

        self.tabControl = ttk.Notebook(self.root)

        self.control_home = ControlHome(self.tabControl, model, self)
        self.define_motors = DefineMotors(self.tabControl, model, self)
        self.preset_options = PresetOptions(self.tabControl, model, self)
        self.feedback = Feedback(self.tabControl, model)

        self.tabControl.pack(expand=1, fill="both")
        self.tabControl.bind("<<NotebookTabChanged>>", self._tab_changed)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # The model is only wired up once every tab exists, so no callback can
        # arrive before there is something to display it on.
        model.register_bridge(self)

        self.root.after(PUMP_INTERVAL_MS, self._pump)

    def run(self) -> None:
        self.model.startup()
        self.root.mainloop()

    # -- thread marshalling ---------------------------------------------------

    def post(self, callback) -> None:
        """Queue ``callback`` to run on the main thread."""
        self._queue.put(callback)

    def _pump(self) -> None:
        """Drain queued callbacks. Runs on the main thread every 50 ms."""
        while True:
            try:
                callback = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:  # pragma: no cover - a bad callback must not
                self.model.LOGGER.exception("Error updating the display")
        if not self._closing:
            self.root.after(PUMP_INTERVAL_MS, self._pump)

    # -- UiBridge -------------------------------------------------------------

    def status(self, message: str) -> None:
        self.post(lambda: self.control_home.set_status(message))

    def state_changed(self, state: MachineState) -> None:
        def apply() -> None:
            self.control_home.refresh(state)
            self.define_motors.refresh(state)
            self.preset_options.refresh(state)

        self.post(apply)

    def progress(self, fraction: float, label: str) -> None:
        self.post(lambda: self.control_home.show_progress(fraction, label))

    def progress_done(self, artifact: Optional[str]) -> None:
        self.post(lambda: self.control_home.finish_progress(artifact))

    def problem(self, title: str, message: str) -> None:
        self.post(lambda: messagebox.showerror(title, message, parent=self.root))

    # -- window events --------------------------------------------------------

    def _tab_changed(self, _event: object) -> None:
        index = self.tabControl.index(self.tabControl.select())
        for position, tab in enumerate(
            (self.control_home, self.define_motors, self.preset_options, self.feedback)
        ):
            if position == index:
                tab.onSelect()

    def _on_close(self) -> None:
        """Stop the machine before the window disappears.

        Closing the window used to leave the pistons running: nothing was bound
        to the close button, so the process exited with Run_2 still set.
        """
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
