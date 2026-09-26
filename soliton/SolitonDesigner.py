"""Soliton: make one solitary wave travel down the tank.

Why this is not part of the Wave tab
------------------------------------
Everything on the Wave tab is periodic. You pick a shape, a height and a
period, and the array runs it continuously. A solitary wave is the opposite
kind of thing: one push, never repeated, whose shape *in time* is the entire
content of the result. `docs/CALIBRATION_NOTES.md` puts it plainly -- solitary
waves need their own calibration and control path rather than being treated as
one of the existing periodic Wave-tab shapes.

The two honest warnings this tab carries
----------------------------------------
**The heights are targets, not predictions.** Nothing in this repository has
ever measured water height against piston motion. There is no wave gauge and no
camera tracker. So the tab shows what it is asking the pistons to do, says what
wave that is *meant* to make, and gives you a Calibration figure to turn once
you have watched the tank. It does not pretend the two are the same number.

**A soliton needs a curve run.** The stagger between columns is real per-piston
timing, and Curve Offset is the only mechanism on this machine that provides
it -- the controller reads it during a curve run and ignores it completely
during a continuous one. So this tab sends you to Start Curve, and says so,
rather than quietly producing something Start would run as thirty simultaneous
pushes. Whether a curve is loaded on the drives is a separate question, and the
tab says how to find out.
"""

from __future__ import annotations

import math
from logging import Logger, getLogger
from tkinter import Canvas, DoubleVar, IntVar, ttk

from app import params, solitons, tags
from Model import MachineState, Model
from modules.logging.log_utils import LOGGER_NAME
from modules.widgets import RoundedButton
from style import theme

PREVIEW_H = 190

#: Where the still-water line sits in the preview, as a fraction of its height.
WATER_LINE = 0.52

SKY = "#0d2030"
WATER = "#14466b"
SURFACE_LINE = "#4fc3f7"
PISTON = "#30d158"
PISTON_DRY = "#6e6e73"


class SolitonDesigner:
    """The Soliton tab."""

    logger: Logger = getLogger(LOGGER_NAME)

    def __init__(self, root: ttk.Notebook, model: Model, view) -> None:
        self.model = model
        self.view = view
        self.tab = ttk.Frame(root)

        self.depth = IntVar(value=solitons.DEFAULT_DEPTH)
        self.amplitude = IntVar(value=solitons.DEFAULT_AMPLITUDE)
        self.pitch = IntVar(value=solitons.DEFAULT_COLUMN_PITCH)
        self.gain = DoubleVar(value=1.0)
        self.columns = IntVar(value=solitons.submerged_columns(
            solitons.DEFAULT_DEPTH))

        self._design = None
        self._phase = 0.0
        self._animating = False

        self.tab.columnconfigure(0, weight=1)

        header = ttk.Frame(self.tab, padding=(theme.GUTTER, 16, theme.GUTTER, 8))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Solitary wave", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="One push, not a repeating wave. Set how deep the water is and "
                 "how tall a wave you want, and this works out the rest.",
            style="Dim.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        body = ttk.Frame(
            self.tab, padding=(theme.GUTTER, 0, theme.GUTTER, theme.GUTTER)
        )
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        self._build_sliders(body)
        self._build_preview(body)
        self._build_readout(body)
        self._build_send(body)

        self._changed()
        root.add(self.tab, text="  Soliton  ")

    # -- construction ---------------------------------------------------------

    def _build_sliders(self, parent) -> None:
        card = ttk.Frame(
            parent, style="Card.TFrame",
            padding=(theme.GUTTER, theme.GAP, theme.GUTTER, theme.GAP),
        )
        card.grid(row=0, column=0, sticky="ew")
        card.columnconfigure(1, weight=1)

        self.depth_label = self._slider_row(
            card, 0, "WATER DEPTH", self.depth,
            solitons.MIN_DEPTH, solitons.MAX_DEPTH,
        )
        self.amplitude_label = self._slider_row(
            card, 1, "WAVE HEIGHT", self.amplitude,
            solitons.MIN_AMPLITUDE, solitons.MAX_AMPLITUDE,
        )
        self.columns_label = self._slider_row(
            card, 2, "COLUMNS USED", self.columns, 1, tags.COLUMN_COUNT,
        )

        extras = ttk.Frame(card, style="Card.TFrame")
        extras.grid(row=3, column=0, columnspan=3, sticky="w",
                    pady=(theme.GAP, 0))

        ttk.Label(extras, text="COLUMN SPACING", style="CardDim.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.pitch_entry = ttk.Entry(extras, width=6)
        self.pitch_entry.insert(0, str(solitons.DEFAULT_COLUMN_PITCH))
        self.pitch_entry.grid(row=0, column=1, padx=(theme.TIGHT, 2))
        self.pitch_entry.bind("<KeyRelease>", lambda _e: self._changed())
        ttk.Label(extras, text="mm apart, along the tank",
                  style="CardDim.TLabel").grid(row=0, column=2, sticky="w")

        ttk.Label(extras, text="CALIBRATION", style="CardDim.TLabel").grid(
            row=0, column=3, sticky="w", padx=(theme.GUTTER, 0)
        )
        self.gain_entry = ttk.Entry(extras, width=6)
        self.gain_entry.insert(0, "1.0")
        self.gain_entry.grid(row=0, column=4, padx=(theme.TIGHT, 2))
        self.gain_entry.bind("<KeyRelease>", lambda _e: self._changed())
        # Named for what it is for. The stroke is a recipe and the operator is
        # the only instrument in the room, so this is how they correct it.
        ttk.Label(extras, text="x the push, once you have seen the tank",
                  style="CardDim.TLabel").grid(row=0, column=5, sticky="w")

    def _slider_row(self, card, row, label, variable, low, high):
        ttk.Label(card, text=label, style="CardDim.TLabel").grid(
            row=row, column=0, sticky="w", pady=(0 if row == 0 else theme.GAP, 0)
        )
        scale = ttk.Scale(card, from_=low, to=high, variable=variable,
                          command=lambda _v: self._changed())
        scale.grid(row=row, column=1, sticky="ew", padx=theme.GAP,
                   pady=(0 if row == 0 else theme.GAP, 0))
        value = ttk.Label(card, text="", style="Card.TLabel", width=14)
        value.grid(row=row, column=2, sticky="w",
                   pady=(0 if row == 0 else theme.GAP, 0))
        return value

    def _build_preview(self, parent) -> None:
        holder = ttk.Frame(parent)
        holder.grid(row=1, column=0, sticky="ew", pady=(theme.GAP, 0))
        holder.columnconfigure(0, weight=1)

        self.preview = Canvas(holder, height=PREVIEW_H, highlightthickness=0,
                              bd=0, background=SKY)
        self.preview.grid(row=0, column=0, sticky="ew")
        self.preview.bind("<Configure>", lambda _e: self._draw())

        self.warning = ttk.Label(holder, text="", style="Dim.TLabel",
                                 wraplength=1000, justify="left")
        self.warning.grid(row=1, column=0, sticky="w", pady=(theme.TIGHT, 0))

    def _build_readout(self, parent) -> None:
        card = ttk.Frame(
            parent, style="Card.TFrame",
            padding=(theme.GUTTER, theme.GAP, theme.GUTTER, theme.GAP),
        )
        card.grid(row=2, column=0, sticky="ew", pady=(theme.GAP, 0))

        self.readout = {}
        fields = (
            ("speed", "TRAVELS AT"),
            ("width", "WAVE WIDTH"),
            ("stroke", "PISTONS PUSH"),
            ("peak", "FASTEST"),
            ("duration", "PUSH LASTS"),
            ("stagger", "COLUMN STAGGER"),
        )
        for column, (key, label) in enumerate(fields):
            ttk.Label(card, text=label, style="CardDim.TLabel").grid(
                row=0, column=column, sticky="w", padx=(0, theme.GUTTER)
            )
            value = ttk.Label(card, text="", style="Card.TLabel")
            value.grid(row=1, column=column, sticky="w",
                       padx=(0, theme.GUTTER), pady=(2, 0))
            self.readout[key] = value

    def _build_send(self, parent) -> None:
        row = ttk.Frame(parent)
        row.grid(row=3, column=0, sticky="ew", pady=(theme.GAP, 0))
        row.columnconfigure(1, weight=1)

        self.send_button = RoundedButton(
            row, "Send to the pistons", self.send, variant="primary",
            size="large", width=210,
        )
        self.send_button.grid(row=0, column=0)

        self.result = ttk.Label(row, text="", style="Dim.TLabel",
                                wraplength=820, justify="left")
        self.result.grid(row=0, column=1, sticky="w", padx=(theme.GAP, 0))

    # -- behaviour ------------------------------------------------------------

    def _number(self, entry, fallback):
        """A number the operator is part-way through typing must not crash."""
        try:
            return float(entry.get().strip())
        except (TypeError, ValueError):
            return fallback

    def _changed(self) -> None:
        pitch = self._number(self.pitch_entry, solitons.DEFAULT_COLUMN_PITCH) \
            if hasattr(self, "pitch_entry") else solitons.DEFAULT_COLUMN_PITCH
        gain = self._number(self.gain_entry, 1.0) \
            if hasattr(self, "gain_entry") else 1.0

        design = solitons.design(
            self.depth.get(), self.amplitude.get(), gain=max(gain, 0.01),
            pitch=max(pitch, 1.0), columns=int(self.columns.get()),
        )
        self._design = design

        self.depth_label.configure(text="{0} mm".format(design.depth))
        self.amplitude_label.configure(
            text="{0} mm  ({1:.0%})".format(design.amplitude, design.steepness)
        )
        used = int(self.columns.get())
        self.columns_label.configure(
            text="{0}  (pistons 1-{1})".format(
                used, used * tags.ROWS_PER_COLUMN)
        )

        self.readout["speed"].configure(
            text="{0:.0f} mm/s".format(design.celerity))
        self.readout["width"].configure(text="{0:.0f} mm".format(design.width))
        self.readout["stroke"].configure(
            text="{0:.0f} mm".format(design.stroke))
        self.readout["peak"].configure(
            text="{0:.0f} mm/s".format(design.peak_speed))
        self.readout["duration"].configure(
            text="{0:.2f} s".format(design.duration))
        if design.offsets:
            spread = max(design.offsets.values()) - min(design.offsets.values())
            self.readout["stagger"].configure(
                text="{0:.2f} s".format(spread / solitons.OFFSET_TICKS_PER_SECOND))
        else:
            self.readout["stagger"].configure(text="-")

        notes = list(design.problems) + list(design.warnings)
        self.warning.configure(text="\n".join(notes))

        self._draw()

    def _draw(self) -> None:
        """The tank from the side: the bank at the left, the soliton leaving."""
        c = self.preview
        c.delete("all")
        design = self._design
        if design is None:
            return

        width = max(c.winfo_width(), 400)
        margin = 16
        usable = width - margin * 2
        water_y = PREVIEW_H * WATER_LINE

        c.create_rectangle(0, water_y, width, PREVIEW_H, fill=WATER, outline="")

        # The tank is drawn long enough to show the wave clear of the bank,
        # so the preview is scaled in real millimetres rather than fractions.
        bank_mm = design.pitch * max(int(self.columns.get()) - 1, 1)
        span_mm = max(bank_mm * 4.0, design.width * 6.0, 1.0)
        per_mm = usable / span_mm

        # The soliton, sech^2, travelling away from the bank.
        crest_mm = bank_mm + self._phase * (span_mm - bank_mm)
        scale = min(PREVIEW_H * 0.30 / max(design.amplitude, 1), 0.45)
        points = []
        for step in range(int(usable)):
            x_mm = step / per_mm
            arg = (x_mm - crest_mm) / max(design.width, 1.0)
            # sech^2, guarded: cosh overflows long before the wave is visible.
            if abs(arg) > 20:
                rise = 0.0
            else:
                rise = design.amplitude / (math.cosh(arg) ** 2)
            points.extend((margin + step, water_y - rise * scale))
        if len(points) >= 4:
            c.create_line(*points, fill=SURFACE_LINE, width=2, smooth=True)

        # The piston bank: one bar per column, stepping higher along the tank,
        # because that is how it is built. Dry columns are drawn grey.
        used = int(self.columns.get())
        for column in range(tags.COLUMN_COUNT):
            x = margin + column * design.pitch * per_mm
            if x > width - margin:
                break
            # Column 1 is deepest; each one after it sits higher.
            lift = column * (PREVIEW_H * 0.045)
            top = water_y - PREVIEW_H * 0.34 - lift
            foot = water_y - lift + PREVIEW_H * 0.06
            dry = column >= used
            c.create_rectangle(x - 4, top, x + 4, foot,
                               fill=PISTON_DRY if dry else PISTON, outline="")

        c.create_text(
            margin, PREVIEW_H - 6, anchor="sw",
            text="{0} mm of water  ·  {1} mm wave  ·  bank at the left"
                 .format(design.depth, design.amplitude),
            fill=theme.LABEL_TERTIARY, font=("Segoe UI", 8),
        )

    def _tick(self) -> None:
        if not self._animating:
            return
        # One pass, then round again: a soliton does not oscillate, so the
        # preview shows it leaving rather than bobbing in place.
        self._phase = (self._phase + 0.006) % 1.0
        self._draw()
        self.tab.after(40, self._tick)

    # -- lifecycle ------------------------------------------------------------

    def onSelect(self) -> None:
        if not self._animating:
            self._animating = True
            self._tick()
        self.refresh(self.model.state)

    def onLeave(self) -> None:
        self._animating = False

    def refresh(self, state: MachineState) -> None:
        busy = state in (MachineState.PREPARING, MachineState.RUNNING)
        design = self._design
        ok = design is not None and design.runnable
        self.send_button.set_state("normal" if ok and not busy else "disabled")
        if busy:
            self.result.configure(text="The machine is busy. Stop it first.")
        elif design is not None and design.problems:
            self.result.configure(
                text="Adjust the wave until the problem above is gone."
            )

    # -- sending --------------------------------------------------------------

    def send(self) -> None:
        design = self._design
        if design is None or not design.runnable:
            return

        used = int(self.columns.get())
        axes = solitons.axes_for(used)
        self.model.set_selection(axes)
        if not self.model.sets:
            self.model.create_set()

        values = params.defaults()
        low = params.BY_NAME["Position 1"].minimum
        # The push starts at the bottom of the travel and goes down into the
        # water. Position 2 is the far end of the stroke, which for a plunger
        # entering from above is the deepest point it reaches.
        top = int(max(low, params.BY_NAME["Position 2"].maximum - design.stroke))
        values.update({
            "Position 1": top,
            "Position 2": params.BY_NAME["Position 2"].maximum,
            "Speed 1": int(min(round(design.peak_speed),
                               params.BY_NAME["Speed 1"].maximum)),
            "Speed 2": int(min(round(design.peak_speed),
                               params.BY_NAME["Speed 1"].maximum)),
            "Profile": solitons_profile(),
            "Move Type": 0,
            # A stored monotone curve, played once. Curve 1 is "scurve out" in
            # the archived drive configuration -- a smooth one-way ramp, which
            # is the right shape. Whether it is still loaded on all thirty
            # drives is unverified; tools/live_curve_probe.py answers that and
            # has never been run.
            "Curve ID": 1,
            "Time Scale": int(round(design.duration * 100)),
            "Amplitude Scale": 100,
        })

        count = 0
        for motor_set in self.model.sets:
            for motor in motor_set:
                motor.update_params(values)
                count += 1

        # The stagger. Keyed by where the piston sits, never by its axis: axis 0
        # is piston 30, at the far end of the bank. Indexing by axis would fire
        # the columns in the opposite order and march the wave into the wall.
        for motor_set in self.model.sets:
            for motor in motor_set:
                place = tags.display_number(motor.axis) - 1
                column = place // tags.ROWS_PER_COLUMN
                motor.set_param("Curve Offset", design.offsets.get(column, 0))

        self.model.mark_unprepared()

        message = (
            "Soliton sent to {0} piston(s): {1} mm push, {2:.0f} mm/s at its "
            "fastest, {3:.2f} s long, columns staggered over {4:.2f} s.".format(
                count, int(design.stroke), design.peak_speed, design.duration,
                (max(design.offsets.values()) - min(design.offsets.values()))
                / solitons.OFFSET_TICKS_PER_SECOND if design.offsets else 0.0,
            )
        )
        how = (
            "  Go to Operate and press Start Curve, not Start. The stagger "
            "between columns lives in Curve Offset, which the controller reads "
            "only during a curve run -- an ordinary Start would fire every "
            "column at once and make a hump rather than a soliton. If nothing "
            "moves, no curve is loaded for Curve ID 1 on these drives; run "
            "tools/live_curve_probe.py to find out."
        )
        self.result.configure(text=message + how)
        self.view.status(message)
        self.logger.info("Soliton applied - %s", message)
        self.view.refresh_all()


def solitons_profile() -> int:
    """The motion profile a soliton push wants.

    S-curve: eased at both ends, no hard start or stop. A trapezoidal push
    would start and end with a step in acceleration, which puts a transient in
    the water that is not part of the wave being asked for.
    """
    return 2
