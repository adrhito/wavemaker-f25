"""Diagnostics: why a piston is stuck, not merely that it is.

The Feedback tab says what happened. This tab answers the next question, which
is the one that actually costs time at the machine: *why*. It reads every
sensor a piston has, reasons over the combination in :mod:`app.diagnostics`,
and prints a cause with something to do about it.

Reading is safe at any time -- the PLC transport locks per operation, so this
works while the machine is running, which is often the only moment a fault is
visible. The movement test is the one thing here that moves anything, so it is
off by default and asks first.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import BooleanVar, StringVar, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import List, Optional

from app import tags
from Model import Model
from modules.widgets import RoundedButton
from style import theme

#: Colour per verdict, matching the rest of the application.
VERDICT_COLOUR = {
    "ok": "#30d158",
    "warn": "#ffd60a",
    "bad": "#ff453a",
    "unknown": "#8e8e93",
}
#: Colour per cause, so the kind of problem reads before the words do.
CAUSE_COLOUR = {
    "Mechanical": "#ff9f0a",
    "Thermal": "#ff453a",
    "Electrical": "#ff453a",
    "Drive": "#ff375f",
    "Software": "#0a84ff",
    "Unknown": "#8e8e93",
}


class Diagnostics:
    """The Diagnostics tab."""

    def __init__(self, root: ttk.Notebook, model: Model) -> None:
        self.tab = ttk.Frame(root)
        self.model = model
        self._running = False

        self.tab.rowconfigure(1, weight=1)
        self.tab.columnconfigure(0, weight=1)

        title = ttk.Frame(self.tab, padding=(25, 20, 25, 0))
        title.grid(row=0, column=0, sticky="ew")
        ttk.Label(title, text="Diagnostics", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            title,
            text="Reads every sensor a piston has -- drive status, warnings, "
            "temperature, position, selection and the parameters actually held "
            "on the drive -- and works out what is wrong with it.",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        body = ttk.Frame(self.tab, padding=(25, 12, 25, 25))
        body.grid(row=1, column=0, sticky="nsew")
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        controls = ttk.Frame(body)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        ttk.Label(controls, text="Piston", style="Dim.TLabel").grid(row=0, column=0)
        self.axis_var = StringVar(value="1")
        self.axis_box = ttk.Combobox(
            controls, textvariable=self.axis_var, state="readonly", width=4,
            values=[str(n) for n in range(1, tags.MOTOR_COUNT + 1)],
        )
        self.axis_box.grid(row=0, column=1, padx=(6, 10))

        self.run_button = RoundedButton(
            controls, "Diagnose", self.diagnose_one, width=104
        )
        self.run_button.grid(row=0, column=2)
        self.all_button = RoundedButton(
            controls, "Diagnose All", self.diagnose_all, size="small", width=104
        )
        self.all_button.grid(row=0, column=3, padx=(theme.TIGHT, 0))
        self.faulty_button = RoundedButton(
            controls, "Only the unhappy ones", self.diagnose_faulty,
            size="small", width=160,
        )
        self.faulty_button.grid(row=0, column=4, padx=(theme.TIGHT, 0))

        self.probe_var = BooleanVar(value=False)
        ttk.Checkbutton(
            controls, text="Include movement test (moves the piston 10 mm)",
            variable=self.probe_var,
        ).grid(row=0, column=5, padx=(theme.GUTTER, 0))

        self.output = ScrolledText(body, width=150, height=30, wrap=tk.WORD)
        self.output.grid(row=1, column=0, sticky="nsew")
        self.output.configure(
            bg="black", fg=theme.LABEL, state=tk.DISABLED,
            font=("Consolas", 9), insertbackground=theme.LABEL,
        )
        for name, colour in list(VERDICT_COLOUR.items()) + list(CAUSE_COLOUR.items()):
            self.output.tag_configure(name, foreground=colour)
        self.output.tag_configure("heading", foreground="#f5f5f7",
                                  font=("Segoe UI Semibold", 11))
        self.output.tag_configure("sub", foreground="#8e8e93")
        self.output.tag_configure("action", foreground="#64d2ff")

        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, sticky="w", pady=(10, 0))
        RoundedButton(buttons, "Copy", self.copy, size="small", width=70).grid(
            row=0, column=0
        )
        RoundedButton(buttons, "Clear", self.clear, size="small", width=70).grid(
            row=0, column=1, padx=(theme.TIGHT, 0)
        )

        self._write_intro()
        root.add(self.tab, text="   Diagnostics")

    # -- tab hooks ------------------------------------------------------------

    def onSelect(self) -> None:
        pass

    def onLeave(self) -> None:
        pass

    def refresh(self, _state=None) -> None:
        pass

    # -- running --------------------------------------------------------------

    def _selected_axis(self) -> Optional[int]:
        """The box shows the number on the tank, which is not the axis.

        Pistons are named by where they sit, so piston 15 is not axis 14 --
        see :func:`app.tags.display_number`.
        """
        try:
            return tags.axis_from_display(int(self.axis_var.get()))
        except (TypeError, ValueError):
            return None

    def diagnose_one(self) -> None:
        axis = self._selected_axis()
        if axis is None:
            return
        probe = bool(self.probe_var.get())
        if probe and not self._confirm_probe(axis):
            return
        self._start([axis], probe)

    #: Axis order is the reverse of display order, so a report iterated by
    #: axis came out piston 30 first and piston 1 last -- the opposite of the
    #: tank the operator is looking at while reading it.
    @staticmethod
    def _in_display_order(axes):
        return sorted(axes, key=tags.display_number)

    def diagnose_all(self) -> None:
        self._start(self._in_display_order(range(tags.MOTOR_COUNT)), False)

    def diagnose_faulty(self) -> None:
        """Only the pistons the machine is already unhappy about."""
        suspect = set(self.model.unhomed_axes)             | set(self.model.lagging_axes)             | set(self.model.unreadable_axes)
        if not suspect:
            self._start(self._in_display_order(range(tags.MOTOR_COUNT)), False)
            return
        self._start(self._in_display_order(suspect), False)

    def _confirm_probe(self, axis: int) -> bool:
        return messagebox.askyesno(
            "Move piston {0}?".format(tags.display_number(axis)),
            "The movement test asks piston {0} to move 10 mm and measures how "
            "far it actually goes.\n\n"
            "The result can help investigate a motion problem, but unchanged "
            "feedback alone cannot prove a jam. Make sure the "
            "chamber is clear.\n\n"
            "Continue?".format(tags.display_number(axis)),
            parent=self.tab,
        )

    def _start(self, axes: List[int], probe: bool) -> None:
        if self._running:
            return
        self._running = True
        self.clear()
        self._append(
            "Reading {0} piston(s)...\n\n".format(len(axes)), "sub"
        )
        thread = threading.Thread(
            target=self._work, args=(axes, probe), name="Diagnose", daemon=True
        )
        thread.start()

    def _work(self, axes: List[int], probe: bool) -> None:
        try:
            for axis in axes:
                try:
                    report = self.model.diagnose_axis(axis, probe=probe)
                except Exception as exc:  # noqa: BLE001 - report, never crash
                    self.tab.after(
                        0, self._append,
                        "Piston {0}: could not be read ({1})\n".format(
                            tags.display_number(axis), exc),
                        "bad",
                    )
                    continue
                self.tab.after(0, self._show, report, len(axes) == 1)
        finally:
            self.tab.after(0, self._finished)

    def _finished(self) -> None:
        self._running = False
        self._append("\nDone.\n", "sub")

    # -- output ---------------------------------------------------------------

    def _show(self, report, verbose: bool) -> None:
        healthy = report.healthy
        if not verbose and healthy:
            self._append("OK    {0}\n".format(report.summary), "ok")
            return

        self._append("\n{0}\n".format(report.summary), "heading")

        for finding in report.findings:
            colour = CAUSE_COLOUR.get(finding.cause, "Unknown")
            self._append("\n  [{0}] ".format(finding.cause.upper()), colour)
            self._append("{0}".format(finding.headline), "heading")
            if finding.confidence != "certain":
                self._append("  (likely)", "sub")
            self._append("\n    {0}\n".format(finding.detail))
            self._append("    -> {0}\n".format(finding.action), "action")

        if not report.findings:
            self._append(
                "\n  Every sensor on this piston reads normal.\n", "ok"
            )

        if verbose:
            self._append("\n  What was read:\n", "sub")
            for check in report.checks:
                self._append("    {0:<34}".format(check.name + ":"), "sub")
                self._append("{0}\n".format(check.reading),
                             check.verdict if check.verdict != "ok" else "ok")

    def _append(self, text: str, tag: Optional[str] = None) -> None:
        self.output.configure(state=tk.NORMAL)
        if tag:
            self.output.insert(tk.END, text, tag)
        else:
            self.output.insert(tk.END, text)
        self.output.see(tk.END)
        self.output.configure(state=tk.DISABLED)

    def _write_intro(self) -> None:
        self._append(
            "Pick a piston and press Diagnose.\n\n"
            "Every sensor it has is read at once -- drive status and warn "
            "words, motor and controller temperature, supply voltage, the "
            "position sensor, its following error, whether the PLC has it "
            "selected, and the parameters the drive is actually holding -- and "
            "the combination is reasoned about rather than listed. A hot motor "
            "that is also lagging is reported as hot, because the lag is the "
            "symptom.\n\n"
            "Nothing here moves the machine unless you tick the movement "
            "test.\n",
            "sub",
        )

    def clear(self) -> None:
        self.output.configure(state=tk.NORMAL)
        self.output.delete("1.0", tk.END)
        self.output.configure(state=tk.DISABLED)

    def copy(self) -> None:
        self.tab.clipboard_clear()
        self.tab.clipboard_append(self.output.get("1.0", tk.END))
