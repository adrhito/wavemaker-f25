"""Soliton trial controls for the vertically moving floor array.

This tab separates a requested water surface from the actual floor command.
The operator stages the floor, waits for the water to settle, then fires one
bounded upward move.  No image or simulation is presented as a measured wave.
"""

from __future__ import annotations

import json
import math
from tkinter import Canvas, IntVar, StringVar, TclError, Toplevel, messagebox, ttk

from app import soliton_calibration, soliton_records, tags
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
        self.station = StringVar(value="")
        self._suggestion = None
        self._suggestion_key = None
        self._calibration_after_id = None

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
        ttk.Label(controls, text="MEASUREMENT STATION",
                  style="CardDim.TLabel").grid(
                      row=4, column=0, sticky="w", pady=(theme.TIGHT, 0))
        ttk.Entry(controls, textvariable=self.station, width=8).grid(
            row=4, column=2, sticky="w", pady=(theme.TIGHT, 0))
        ttk.Label(controls, text="mm along tank from floor array; same mark each run",
                  style="CardDim.TLabel").grid(
                      row=4, column=3, sticky="w", padx=(theme.GAP, 0),
                      pady=(theme.TIGHT, 0))

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
        self.calibration = ttk.Label(body, text="", style="Dim.TLabel",
                                     wraplength=1050, justify="left")
        self.calibration.grid(row=4, column=0, sticky="w", pady=(theme.TIGHT, 0))

        actions = ttk.Frame(body)
        actions.grid(row=5, column=0, sticky="ew", pady=(theme.GAP, 0))
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
        self.suggest_button = RoundedButton(
            actions, "Apply measured lift", self.apply_suggestion,
            variant="secondary", size="large", width=205)
        self.suggest_button.grid(row=0, column=3, padx=(theme.GAP, 0))

        self.result = ttk.Label(body, text="", style="Dim.TLabel",
                                wraplength=1050, justify="left")
        self.result.grid(row=6, column=0, sticky="w", pady=(theme.GAP, 0))
        self.caveat = ttk.Label(
            body, style="Dim.TLabel", wraplength=1050, justify="left",
            text="Experimental floor pulse: requested water height and width are "
                 "targets, not measured outcomes. The drive uses a bounded "
                 "S-curve; its safe fastest stop is not yet known. The floor "
                 "stays raised after firing. Stage again to lower it.")
        self.caveat.grid(row=7, column=0, sticky="w", pady=(theme.GAP, 0))

        for variable in (self.crest, self.width, self.depth, self.lift,
                         self.station):
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
        self._station_mm()  # Reject a malformed optional station before staging.
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

    def _station_mm(self):
        value = self.station.get().strip()
        if not value:
            return None
        try:
            station = float(value)
        except (ValueError, TclError) as exc:
            raise ValueError("Measurement station must be millimetres from the floor array.") from exc
        if not math.isfinite(station) or station < 0:
            raise ValueError("Measurement station must be a finite nonnegative distance.")
        return station

    def _calibration_key(self):
        try:
            trial = self._trial()
        except ValueError:
            return None
        station = self._station_mm()
        if station is None or not self.model.sets:
            return None
        pistons = tuple(sorted(tags.display_number(axis)
                               for axis in self.model.live_axes))
        return trial.target, pistons, station

    def _schedule_calibration(self, force=False):
        key = self._calibration_key()
        if not force and key == self._suggestion_key:
            return
        if self._calibration_after_id is not None:
            self.tab.after_cancel(self._calibration_after_id)
            self._calibration_after_id = None
        self._suggestion = None
        self._suggestion_key = None
        if key is None:
            self.calibration.configure(
                text="Enter a fixed measurement station and select floor sections "
                     "to check recorded calibration trials.")
            self.suggest_button.set_state("disabled")
            return
        self.calibration.configure(text="Checking measured trials for this setup...")
        self.suggest_button.set_state("disabled")
        self._calibration_after_id = self.tab.after(180, self._update_calibration)

    def _update_calibration(self):
        self._calibration_after_id = None
        key = self._calibration_key()
        if key is None:
            self._schedule_calibration(force=True)
            return
        target, pistons, station = key
        self._suggestion_key = key
        try:
            assessment = soliton_calibration.assess_lift(
                target, pistons, station)
        except OSError as exc:
            self.calibration.configure(text="Cannot read soliton trial records: {0}".format(exc))
            self.suggest_button.set_state("disabled")
            return
        self._suggestion = assessment.suggestion
        self.calibration.configure(text=assessment.message)
        self.refresh(self.model.state)

    def apply_suggestion(self):
        if (self._suggestion is None or
                self._calibration_key() != self._suggestion_key):
            self.result.configure(text="Recheck measured trials before applying a lift.")
            return
        self.lift.set(self._suggestion.floor_lift_mm)
        self.result.configure(
            text="Measured lift suggestion applied. Stage the floor again with "
                 "these settings before firing.")

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
        if self.model.fire_soliton(trial, planned_station_mm=self._station_mm()):
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
        fields = {}
        try:
            saved = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        station = saved.get("measurement_station_mm")
        if station is None:
            station = saved.get("planned_measurement_station_mm")
        video = saved.get("observed_width_method") == "video_timing"
        initial = {
            "crest": saved.get("observed_crest_rise_mm"),
            "total": saved.get("observed_crest_to_trough_mm"),
            "width": None if video else saved.get("observed_fwhm_width_mm"),
            "spacing": saved.get("video_station_spacing_mm") if video else None,
            "transit": saved.get("video_crest_transit_s") if video else None,
            "duration": saved.get("video_half_height_duration_s") if video else None,
            "station": station,
            "notes": saved.get("measurement_notes"),
        }
        labels = (
            ("crest", "Crest rise above still water (mm)"),
            ("total", "Crest-to-trough height (mm)"),
            ("width", "Width at half crest rise, if measured directly (mm)"),
            ("spacing", "Video: distance between two station marks (mm)"),
            ("transit", "Video: crest travel time between marks (s)"),
            ("duration", "Video: time above half height at first mark (s)"),
            ("station", "First station from floor array (mm)"),
            ("notes", "Notes / video or photo filename"),
        )
        for row, (name, label) in enumerate(labels):
            value = initial[name]
            variable = StringVar(value=str(value) if value is not None else "")
            ttk.Label(content, text=label).grid(row=row, column=0, sticky="w",
                                                  pady=(0, theme.GAP))
            ttk.Entry(content, textvariable=variable, width=38).grid(
                row=row, column=1, sticky="ew", padx=(theme.GAP, 0),
                pady=(0, theme.GAP))
            fields[name] = variable
        ttk.Label(
            content,
            text="Enter a direct width OR all three video timings. Video width = "
                 "station spacing × half-height duration ÷ crest travel time.",
            wraplength=600, justify="left",
        ).grid(row=len(labels), column=0, columnspan=2, sticky="w",
               pady=(0, theme.GAP))

        def save() -> None:
            try:
                saved_width = soliton_records.record_observation(
                    record, fields["crest"].get(), fields["total"].get(),
                    fields["station"].get(), fields["notes"].get(),
                    fwhm_width_mm=fields["width"].get(),
                    video_station_spacing_mm=fields["spacing"].get(),
                    video_crest_transit_s=fields["transit"].get(),
                    video_half_height_duration_s=fields["duration"].get())
            except (ValueError, OSError) as exc:
                messagebox.showerror("Check the observation", str(exc), parent=dialog)
                return
            detail = (" Calculated width: {0:.1f} mm.".format(saved_width)
                      if saved_width is not None and fields["spacing"].get().strip()
                      else "")
            self.result.configure(
                text="Observed water measurements saved to {0}.{1}".format(
                    record.name, detail))
            self.view.status("Soliton observation saved.")
            self._schedule_calibration(force=True)
            dialog.destroy()

        ttk.Button(content, text="Save observation", command=save).grid(
            row=len(labels) + 1, column=1, sticky="e")
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
        key = self._calibration_key()
        if key != self._suggestion_key and self._calibration_after_id is None:
            self._schedule_calibration()
        try:
            current_lift = self.lift.get()
        except TclError:
            current_lift = None
        self.suggest_button.set_state(
            "normal" if self._suggestion is not None and not busy and
            key == self._suggestion_key and
            current_lift != self._suggestion.floor_lift_mm else "disabled")

    def onSelect(self) -> None:
        self.refresh(self.model.state)
        self._schedule_calibration(force=True)
