"""Soliton trial controls for the vertically moving floor array.

This tab separates a requested water surface from the actual floor command.
The operator stages the floor, waits for the water to settle, then fires one
bounded upward move.  No image or simulation is presented as a measured wave.
"""

from __future__ import annotations

from tkinter import Canvas, IntVar, StringVar, TclError, Toplevel, messagebox, ttk

from app import soliton_records
from app.solitons import SolitaryTarget, SolitonTrial
from Model import MachineState, Model
from modules.widgets import RoundedButton
from style import theme


class SolitonDesigner:
    """A target-wave preview and an explicitly staged one-shot floor pulse."""

    def __init__(self, root: ttk.Notebook, model: Model, view) -> None:
        self.model = model
        self.view = view
        self.tab = ttk.Frame(root)
        self.tab.columnconfigure(0, weight=1)

        self.crest = IntVar(value=30)
        # Start near the theory width for 30 mm over 150 mm of water, so the
        # initial preview is a plausible first-order solitary-wave target.
        self.width = IntVar(value=int(round(
            SolitaryTarget(30, 1, 150).theoretical_width_mm)))
        self.depth = IntVar(value=150)
        self.lift = IntVar(value=60)

        header = ttk.Frame(self.tab, padding=(theme.GUTTER, 16, theme.GUTTER, 8))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Soliton trial", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Choose a water-wave target, stage the floor, then fire one "
            "upward pulse after the water settles.",
            style="Dim.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        body = ttk.Frame(self.tab, padding=(theme.GUTTER, 0, theme.GUTTER, theme.GUTTER))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        controls = ttk.Frame(body, style="Card.TFrame",
                             padding=(theme.GUTTER, theme.GAP,
                                      theme.GUTTER, theme.GAP))
        controls.grid(row=0, column=0, sticky="ew")
        controls.columnconfigure(1, weight=1)

        self._control(controls, 0, "TARGET CREST RISE", self.crest, 10, 150,
                      "mm above still water")
        self._control(controls, 1, "TARGET WIDTH", self.width, 300, 5000,
                      "mm along the tank, at half height")
        self._control(controls, 2, "STILL-WATER DEPTH", self.depth, 50, 500,
                      "mm; measure for each trial")
        self._control(controls, 3, "FLOOR LIFT", self.lift, 1, 120,
                      "mm of motor travel; uncalibrated")

        self.preview = Canvas(body, height=150, highlightthickness=0, bd=0,
                              background="#0d2030")
        self.preview.grid(row=1, column=0, sticky="ew", pady=(theme.GAP, 0))
        self.preview.bind("<Configure>", lambda _event: self._draw_preview())

        self.summary = ttk.Label(body, text="", style="Dim.TLabel",
                                 wraplength=1050, justify="left")
        self.summary.grid(row=2, column=0, sticky="w", pady=(theme.GAP, 0))
        self.warning = ttk.Label(body, text="", style="Dim.TLabel",
                                 wraplength=1050, justify="left")
        self.warning.grid(row=3, column=0, sticky="w", pady=(theme.TIGHT, 0))

        actions = ttk.Frame(body)
        actions.grid(row=4, column=0, sticky="ew", pady=(theme.GAP, 0))
        self.stage_button = RoundedButton(
            actions, "1. Stage floor", self.stage, variant="secondary",
            size="large", width=185)
        self.stage_button.grid(row=0, column=0, padx=(0, theme.GAP))
        self.fire_button = RoundedButton(
            actions, "2. Fire one pulse", self.fire, variant="primary",
            size="large", width=190)
        self.fire_button.grid(row=0, column=1)
        self.observe_button = RoundedButton(
            actions, "Record observed wave", self.record_observed,
            variant="secondary", size="large", width=220)
        self.observe_button.grid(row=0, column=2, padx=(theme.GAP, 0))

        self.result = ttk.Label(body, text="", style="Dim.TLabel",
                                wraplength=1050, justify="left")
        self.result.grid(row=5, column=0, sticky="w", pady=(theme.GAP, 0))
        self.caveat = ttk.Label(
            body, style="Dim.TLabel", wraplength=1050, justify="left",
            text="Experimental floor pulse: requested water height and width are "
                 "targets, not measured outcomes. The drive uses a bounded "
                 "S-curve; its safe fastest stop is not yet known. The floor "
                 "stays raised after firing. Stage again to lower it.")
        self.caveat.grid(row=6, column=0, sticky="w", pady=(theme.GAP, 0))

        for variable in (self.crest, self.width, self.depth, self.lift):
            variable.trace_add("write", lambda *_args: self._changed())
        self._changed()
        root.add(self.tab, text="  Soliton  ")

    def _control(self, parent, row, label, variable, minimum, maximum, detail):
        ttk.Label(parent, text=label, style="CardDim.TLabel").grid(
            row=row, column=0, sticky="w", pady=(theme.TIGHT, 0))
        ttk.Scale(parent, from_=minimum, to=maximum, variable=variable).grid(
            row=row, column=1, sticky="ew", padx=theme.GAP,
            pady=(theme.TIGHT, 0))
        ttk.Entry(parent, textvariable=variable, width=8).grid(
            row=row, column=2, sticky="w", pady=(theme.TIGHT, 0))
        ttk.Label(parent, text=detail, style="CardDim.TLabel").grid(
            row=row, column=3, sticky="w", padx=(theme.GAP, 0),
            pady=(theme.TIGHT, 0))

    def _trial(self) -> SolitonTrial:
        try:
            crest = float(self.crest.get())
            width = float(self.width.get())
            depth = float(self.depth.get())
            lift = int(self.lift.get())
        except (ValueError, TypeError, TclError) as exc:
            # Tk raises TclError while an Entry is temporarily blank during
            # editing.  Keep the controls inactive until it is valid again.
            raise ValueError("Enter positive numbers for all four settings.") from exc
        return SolitonTrial(SolitaryTarget(crest, width, depth), lift)

    def _changed(self) -> None:
        try:
            trial = self._trial()
        except ValueError as exc:
            self.summary.configure(text="")
            self.warning.configure(text=str(exc))
            self.preview.delete("all")
            self.refresh(self.model.state)
            return
        target = trial.target
        self.summary.configure(
            text="Requested: {0:.0f} mm crest rise and {1:.0f} mm width at "
                 "{2:.0f} mm water depth. Trial command: lift the floor "
                 "{3} mm at {4} mm/s maximum; nominal travel about {5:.2f} s "
                 "before acceleration and deceleration.".format(
                     target.crest_height_mm, target.width_mm,
                     target.water_depth_mm, trial.floor_lift_mm,
                     trial.speed_mm_s, trial.nominal_travel_seconds))
        notes = []
        if not target.width_matches_depth:
            notes.append(
                "At this depth and height, first-order solitary-wave theory "
                "predicts about {0:.0f} mm width. The chosen width is an "
                "experimental target, not an exact soliton.".format(
                    target.theoretical_width_mm))
        if trial.speed_limited:
            notes.append(
                "The requested width calls for faster floor motion than the "
                "{0} mm/s trial cap; this pulse will be broader/slower.".format(
                    trial.speed_mm_s))
        if target.crest_height_mm / target.water_depth_mm > 0.3:
            notes.append(
                "Crest rise is more than 30% of water depth; the first-order "
                "preview is a poor physical approximation here.")
        self.warning.configure(text="  ".join(notes))
        self._draw_preview()
        self.refresh(self.model.state)

    def _draw_preview(self) -> None:
        canvas = self.preview
        canvas.delete("all")
        try:
            target = self._trial().target
        except ValueError:
            return
        width = max(canvas.winfo_width(), 300)
        left, right = 24, width - 24
        still = 112
        canvas.create_line(left, still, right, still,
                           fill=theme.LABEL_TERTIARY, dash=(3, 4))
        points = []
        step = max(1, (right - left) // 240)
        for pixel in range(left, right + 1, step):
            x_mm = (pixel - (left + right) / 2.0) * (4.0 * target.width_mm) / (
                right - left)
            rise = target.elevation_mm(x_mm) / target.crest_height_mm
            points.extend((pixel, still - rise * 70))
        canvas.create_line(*points, fill="#4fc3f7", width=2, smooth=True)
        canvas.create_text(left, 137, anchor="sw",
                           text="Requested water profile · full width at half height",
                           fill=theme.LABEL_TERTIARY, font=("Segoe UI", 9))

    def stage(self) -> None:
        try:
            trial = self._trial()
        except ValueError as exc:
            self.result.configure(text=str(exc))
            return
        if self.model.stage_soliton(trial):
            self.result.configure(
                text="Staging the floor. Wait until it reaches the bottom and "
                     "the water is still before firing.")
            self.refresh(self.model.state)

    def fire(self) -> None:
        try:
            trial = self._trial()
        except ValueError as exc:
            self.result.configure(text=str(exc))
            return
        if self.model.fire_soliton(trial):
            self.result.configure(
                text="One pulse requested. Measure the resulting crest and "
                     "width; the floor will remain raised.")
            self.refresh(self.model.state)

    def record_observed(self) -> None:
        """Let the operator add a water measurement to the last trial file."""
        record = self.model.last_soliton_record
        if record is None:
            self.result.configure(text="Fire a pulse before recording its water height.")
            return
        dialog = Toplevel(self.tab)
        dialog.title("Observed soliton trial")
        dialog.transient(self.tab.winfo_toplevel())
        dialog.resizable(False, False)
        content = ttk.Frame(dialog, padding=theme.GUTTER)
        content.grid(sticky="nsew")
        fields = []
        for row, label in enumerate((
                "Crest rise above still water (mm)",
                "Crest-to-trough height (mm)",
                "Measurement station from floor array (mm)",
                "Notes / photo filename")):
            variable = StringVar()
            ttk.Label(content, text=label).grid(row=row, column=0, sticky="w",
                                                  pady=(0, theme.GAP))
            ttk.Entry(content, textvariable=variable, width=38).grid(
                row=row, column=1, sticky="ew", padx=(theme.GAP, 0),
                pady=(0, theme.GAP))
            fields.append(variable)

        def save() -> None:
            try:
                soliton_records.record_observation(
                    record, fields[0].get(), fields[1].get(),
                    fields[2].get(), fields[3].get())
            except (ValueError, OSError) as exc:
                messagebox.showerror("Check the observation", str(exc), parent=dialog)
                return
            self.result.configure(text="Observed water height saved to {0}.".format(
                record.name))
            self.view.status("Soliton observation saved.")
            dialog.destroy()

        ttk.Button(content, text="Save observation", command=save).grid(
            row=len(fields), column=1, sticky="e")
        dialog.grab_set()

    def refresh(self, state: MachineState) -> None:
        try:
            trial = self._trial()
        except ValueError:
            trial = None
        busy = state in (MachineState.PREPARING, MachineState.RUNNING)
        can_stage = bool(self.model.sets and trial and not busy)
        can_fire = bool(trial and not busy and self.model.soliton_ready_for(trial))
        self.stage_button.set_state("normal" if can_stage else "disabled")
        self.fire_button.set_state("normal" if can_fire else "disabled")
        self.observe_button.set_state(
            "normal" if self.model.last_soliton_record is not None and not busy
            else "disabled")

    def onSelect(self) -> None:
        self.refresh(self.model.state)
