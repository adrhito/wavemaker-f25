"""The other fourteen parameters, on demand.

Stroke and speed live on the Operate screen because they are what changes
between runs. Acceleration, jerk, profile, dwell and the curve settings are set
once and then left alone, so they sit behind a button rather than taking up
permanent space and presenting a newcomer with eighteen boxes at once.
"""

from __future__ import annotations

from tkinter import StringVar, Toplevel, messagebox, ttk
from typing import Dict

from app import params
from modules.tooltip import Tooltip
from modules.widgets import RoundedButton
from style import theme

MIXED = "--"

#: Grouped so the panel reads as three short lists, in the order they matter.
GROUPS = (
    ("Motion", ["Position 1", "Position 2", "Speed 1", "Speed 2",
                "Move Type", "Profile"]),
    ("Ramp", ["Accel 1", "Accel 2", "Decel 1", "Decel 2", "Jerk 1", "Jerk 2"]),
    ("Timing and curve", ["Time 1", "Time 2", "Curve ID", "Time Scale",
                          "Amplitude Scale", "Curve Offset"]),
)


class ParameterDialog:
    """Every parameter for one group, grouped and validated."""

    def __init__(self, parent, motor_set, on_close) -> None:
        self.motor_set = motor_set
        self.on_close = on_close
        self._invalid: Dict[str, str] = {}
        self._refreshing = False

        self.top = Toplevel(parent)
        self.top.title("Parameters - {0}".format(motor_set.name))
        self.top.configure(bg=theme.BACKGROUND)
        self.top.transient(parent.winfo_toplevel())
        self.top.resizable(False, False)

        frame = ttk.Frame(self.top, padding=theme.GUTTER)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text=motor_set.name, style="Heading2.TLabel").grid(
            row=0, column=0, columnspan=6, sticky="w"
        )
        ttk.Label(
            frame,
            text="{0} pistons: {1}".format(
                len(motor_set), ", ".join(str(a) for a in motor_set.axes)
            ),
            style="Dim.TLabel",
        ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(2, theme.GAP + 4))

        self.vars: Dict[str, StringVar] = {}
        self.entries: Dict[str, ttk.Entry] = {}

        for column, (title, names) in enumerate(GROUPS):
            block = ttk.Frame(frame)
            block.grid(row=2, column=column, sticky="nw", padx=(0, theme.GUTTER + 8))
            ttk.Label(block, text=title.upper(), style="Group.TLabel").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, theme.TIGHT + 2)
            )
            for index, name in enumerate(names):
                spec = params.BY_NAME[name]
                label = ttk.Label(block, text=name)
                label.grid(row=index + 1, column=0, sticky="e",
                           padx=(0, theme.TIGHT + 2), pady=3)
                Tooltip(label, "{0}\n\nAccepted: {1}".format(
                    spec.help, spec.describe_range()))
                var = StringVar()
                entry = ttk.Entry(block, textvariable=var, width=9, justify="center")
                entry.grid(row=index + 1, column=1, pady=3)
                var.trace_add("write", lambda *_a, _n=name: self._on_typed(_n))
                self.vars[name] = var
                self.entries[name] = entry

        self.message = ttk.Label(frame, text="", style="Dim.TLabel",
                                 wraplength=640, justify="left")
        self.message.grid(row=3, column=0, columnspan=6, sticky="w",
                          pady=(theme.GAP, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=6, sticky="e", pady=(theme.GAP + 4, 0))
        RoundedButton(buttons, "Restore defaults", self._restore,
                      size="small", width=124).grid(row=0, column=0,
                                                    padx=(0, theme.TIGHT))
        RoundedButton(buttons, "Done", self._close, variant="primary",
                      size="small", width=88).grid(row=0, column=1)

        self._load()
        self.top.protocol("WM_DELETE_WINDOW", self._close)
        self.top.grab_set()
        self.top.focus_set()

    def _load(self) -> None:
        self._refreshing = True
        try:
            for spec in params.PARAMS:
                value = self.motor_set.common_value(spec.name)
                self.vars[spec.name].set(MIXED if value is None else str(value))
        finally:
            self._refreshing = False

    def _on_typed(self, name: str) -> None:
        if self._refreshing:
            return
        text = self.vars[name].get()
        if text.strip() in ("", "-", MIXED):
            return
        try:
            value = params.parse(name, text)
        except ValueError as exc:
            self._invalid[name] = str(exc)
            self._show()
            return
        self._invalid.pop(name, None)
        self.motor_set.set_param(name, value)
        self._show()

    def _show(self) -> None:
        self.message.configure(
            text="  ".join(sorted(self._invalid.values())) if self._invalid else ""
        )

    def _restore(self) -> None:
        for name, value in params.defaults().items():
            self.motor_set.set_param(name, value)
        self._invalid.clear()
        self._load()
        self._show()

    def _close(self) -> None:
        if self._invalid:
            if not messagebox.askyesno(
                "Some values were not applied",
                "These are outside the machine's limits and have not been "
                "applied:\n\n{0}\n\nClose anyway?".format(
                    "\n".join(sorted(self._invalid.values()))
                ),
                parent=self.top,
            ):
                return
        self.top.destroy()
        self.on_close()
