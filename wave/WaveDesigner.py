"""Wave: pick a wave, adjust it, send it to the machine.

The rest of the application is expressed in the machine's own terms -- stroke,
speed, acceleration, profile. That is the right vocabulary for someone tuning
the array, and the wrong one for someone who just wants waves.

This tab is the other end of that. You choose a picture of a wave, drag two
sliders labelled Height and Period, watch a preview, and press Send. It works
out the stroke, speed, profile and accelerations underneath.

It does not model water. It converts a description of piston motion into piston
parameters, and it says so.
"""

from __future__ import annotations

from logging import Logger, getLogger
from tkinter import Canvas, DoubleVar, IntVar, StringVar, ttk
from typing import Dict

from app import params, waves
from Model import MachineState, Model
from modules.logging.log_utils import LOGGER_NAME
from modules.widgets import RoundedButton, Segmented
from style import theme

CARD_W = 150
CARD_H = 84
PREVIEW_H = 150


class WaveDesigner:
    """The Wave tab."""

    logger: Logger = getLogger(LOGGER_NAME)

    def __init__(self, root: ttk.Notebook, model: Model, view) -> None:
        self.model = model
        self.view = view
        self.tab = ttk.Frame(root)

        self.shape = waves.SHAPES[1]           # Gentle swell
        self.height = IntVar(value=self.shape.height)
        self.period = DoubleVar(value=self.shape.period)
        self.direction = StringVar(value=waves.TOGETHER)
        self._cards: Dict[str, Canvas] = {}
        self._phase = 0.0
        self._animating = False

        self.tab.columnconfigure(0, weight=1)

        header = ttk.Frame(self.tab, padding=(theme.GUTTER, 16, theme.GUTTER, 8))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Choose a wave", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="Pick a shape, set how big and how fast, then send it to the "
            "pistons.",
            style="Dim.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        body = ttk.Frame(self.tab, padding=(theme.GUTTER, 0, theme.GUTTER, theme.GUTTER))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        self._build_picker(body)
        self._build_sliders(body)
        self._build_preview(body)
        self._build_send(body)

        self._select(self.shape.key)
        root.add(self.tab, text="  Wave  ")

    # -- construction ---------------------------------------------------------

    def _build_picker(self, parent) -> None:
        strip = ttk.Frame(parent)
        strip.grid(row=0, column=0, sticky="w")

        for column, shape in enumerate(waves.SHAPES):
            holder = ttk.Frame(strip)
            holder.grid(row=0, column=column, padx=(0, theme.GAP))

            card = Canvas(
                holder, width=CARD_W, height=CARD_H, highlightthickness=0, bd=0,
                background=theme.SURFACE, cursor="hand2",
            )
            card.grid(row=0, column=0)
            card.bind("<Button-1>", lambda _e, k=shape.key: self._select(k))
            self._cards[shape.key] = card

            label = ttk.Label(holder, text=shape.name, style="Card.TLabel")
            label.grid(row=1, column=0, pady=(theme.TIGHT, 0))
            label.bind("<Button-1>", lambda _e, k=shape.key: self._select(k))

        self.shape_note = ttk.Label(parent, text="", style="Dim.TLabel",
                                    wraplength=900, justify="left")
        self.shape_note.grid(row=1, column=0, sticky="w", pady=(theme.GAP, 0))

    def _build_sliders(self, parent) -> None:
        card = ttk.Frame(parent, style="Card.TFrame",
                         padding=(theme.GUTTER, theme.GAP, theme.GUTTER, theme.GAP))
        card.grid(row=2, column=0, sticky="ew", pady=(theme.GAP, 0))
        card.columnconfigure(1, weight=1)
        card.columnconfigure(4, weight=1)

        ttk.Label(card, text="HEIGHT", style="CardDim.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.height_scale = ttk.Scale(
            card, from_=waves.MIN_HEIGHT, to=waves.MAX_HEIGHT,
            variable=self.height, command=lambda _v: self._changed(),
        )
        self.height_scale.grid(row=0, column=1, sticky="ew", padx=theme.GAP)
        self.height_label = ttk.Label(card, text="", style="Card.TLabel", width=12)
        self.height_label.grid(row=0, column=2, sticky="w")

        ttk.Label(card, text="PERIOD", style="CardDim.TLabel").grid(
            row=1, column=0, sticky="w", pady=(theme.GAP, 0)
        )
        self.period_scale = ttk.Scale(
            card, from_=waves.MIN_PERIOD, to=waves.MAX_PERIOD,
            variable=self.period, command=lambda _v: self._changed(),
        )
        self.period_scale.grid(row=1, column=1, sticky="ew", padx=theme.GAP,
                               pady=(theme.GAP, 0))
        self.period_label = ttk.Label(card, text="", style="Card.TLabel", width=12)
        self.period_label.grid(row=1, column=2, sticky="w", pady=(theme.GAP, 0))

        ttk.Label(card, text="MOVES", style="CardDim.TLabel").grid(
            row=2, column=0, sticky="w", pady=(theme.GAP, 0)
        )
        picker = ttk.Frame(card, style="Card.TFrame")
        picker.grid(row=2, column=1, sticky="w", padx=theme.GAP, pady=(theme.GAP, 0))
        self.direction_control = Segmented(
            picker,
            [
                (waves.TOGETHER, "All together"),
                (waves.TRAVELLING, "Travelling front to back"),
            ],
            command=self._direction_changed,
            width=300,
            height=28,
        )
        self.direction_control.canvas.configure(background=theme.SURFACE)
        self.direction_control.grid(row=0, column=0)

    def _build_preview(self, parent) -> None:
        holder = ttk.Frame(parent)
        holder.grid(row=3, column=0, sticky="ew", pady=(theme.GAP, 0))
        holder.columnconfigure(0, weight=1)

        self.preview = Canvas(
            holder, height=PREVIEW_H, highlightthickness=0, bd=0,
            background="#0d2030",
        )
        self.preview.grid(row=0, column=0, sticky="ew")
        self.preview.bind("<Configure>", lambda _e: self._draw_preview())

        self.warning = ttk.Label(holder, text="", style="Dim.TLabel",
                                 wraplength=900, justify="left")
        self.warning.grid(row=1, column=0, sticky="w", pady=(theme.TIGHT, 0))

    def _build_send(self, parent) -> None:
        row = ttk.Frame(parent)
        row.grid(row=4, column=0, sticky="ew", pady=(theme.GAP, 0))
        row.columnconfigure(1, weight=1)

        self.send_button = RoundedButton(
            row, "Send to the pistons", self.send, variant="primary",
            size="large", width=210,
        )
        self.send_button.grid(row=0, column=0)

        self.result = ttk.Label(row, text="", style="Dim.TLabel",
                                wraplength=700, justify="left")
        self.result.grid(row=0, column=1, sticky="w", padx=(theme.GAP, 0))

    # -- behaviour ------------------------------------------------------------

    def _direction_changed(self, value) -> None:
        """All together, or running front to back along the chamber.

        A travelling wave needs per-column timing, which only the controller's
        curve feature provides -- so this also changes which button runs it.
        """
        self.direction.set(value)
        self._changed()

    def _select(self, key: str) -> None:
        self.shape = waves.BY_KEY[key]
        self.height.set(self.shape.height)
        self.period.set(self.shape.period)
        self._changed()

    def _changed(self) -> None:
        height = waves.clamp_height(int(self.height.get()))
        period = waves.clamp_period(float(self.period.get()))

        self.height_label.configure(text="{0} mm".format(height))
        self.period_label.configure(text="{0:.1f} s".format(period))
        self.shape_note.configure(text=self.shape.description)

        if waves.is_limited(height, period):
            actual = waves.achievable_period(height, period)
            self.warning.configure(
                text="A wave this tall cannot go this fast: the pistons top out "
                "at {0} mm/s, so it will run at about {1:.1f} s instead of "
                "{2:.1f} s. Make it shorter for a faster wave.".format(
                    params.BY_NAME["Speed 1"].maximum, actual, period
                )
            )
        else:
            self.warning.configure(text="")

        self._draw_cards()
        self._draw_preview()

    def _draw_cards(self) -> None:
        for key, card in self._cards.items():
            shape = waves.BY_KEY[key]
            chosen = key == self.shape.key
            card.delete("all")
            card.configure(background=theme.SURFACE_2 if chosen else theme.SURFACE)

            colour = theme.ACCENT if chosen else theme.LABEL_SECONDARY
            points = []
            for step in range(CARD_W - 16):
                t = step / float(CARD_W - 17)
                y = CARD_H / 2.0 - shape.sample(t * 1.5) * (CARD_H / 2.0 - 14)
                points.extend((8 + step, y))
            card.create_line(*points, fill=colour, width=2, smooth=True)
            if chosen:
                card.create_rectangle(
                    1, 1, CARD_W - 1, CARD_H - 1, outline=theme.ACCENT, width=2
                )

    def _draw_preview(self) -> None:
        """The array, seen from the side, doing the chosen wave."""
        c = self.preview
        c.delete("all")
        width = max(c.winfo_width(), 300)
        height = waves.clamp_height(int(self.height.get()))
        low, high = waves.stroke_for(height)

        margin = 20
        usable = width - margin * 2
        columns = 10
        travelling = self.direction.get() == waves.TRAVELLING

        # Surface line: what the water is doing, roughly.
        points = []
        for step in range(int(usable)):
            t = step / float(usable)
            phase = self._phase + (t * 1.5 if travelling else 0.0)
            y = PREVIEW_H * 0.42 - self.shape.sample(phase) * (PREVIEW_H * 0.22)
            points.extend((margin + step, y))
        if len(points) >= 4:
            c.create_line(*points, fill="#4fc3f7", width=2, smooth=True)

        # The pistons below it.
        for column in range(columns):
            x = margin + (column + 0.5) * usable / columns
            phase = self._phase + (column / float(columns) * 1.5 if travelling else 0.0)
            level = (self.shape.sample(phase) + 1.0) / 2.0
            top = PREVIEW_H * 0.58 + (1.0 - level) * (PREVIEW_H * 0.24)
            c.create_rectangle(
                x - 9, top, x + 9, PREVIEW_H - 8,
                fill="#30d158", outline="",
            )

        c.create_text(
            margin, PREVIEW_H - 4, anchor="sw",
            text="{0} to {1} mm".format(low, high),
            fill=theme.LABEL_TERTIARY, font=("Segoe UI", 8),
        )

    def _tick(self) -> None:
        if not self._animating:
            return
        self._phase = (self._phase + 0.02) % 1.0
        self._draw_preview()
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
        self.send_button.set_state(
            "normal" if self.model.sets and not busy else "disabled"
        )
        if busy:
            self.result.configure(text="The machine is busy. Stop it first.")
        elif not self.model.sets:
            self.result.configure(
                text="Choose pistons on the Operate tab, then come back."
            )
        else:
            self.result.configure(text="")

    # -- sending --------------------------------------------------------------

    def send(self) -> None:
        if not self.model.sets:
            self.result.configure(
                text="Choose pistons on the Operate tab, then come back."
            )
            return

        height = waves.clamp_height(int(self.height.get()))
        period = waves.clamp_period(float(self.period.get()))
        values = waves.to_parameters(self.shape, height, period)

        count = 0
        for motor_set in self.model.sets:
            for motor in motor_set:
                motor.update_params(values)
                count += 1

        travelling = self.direction.get() == waves.TRAVELLING
        if travelling:
            offsets = waves.column_offsets(period)
            for motor_set in self.model.sets:
                for motor in motor_set:
                    motor.set_param("Curve Offset", offsets[motor.axis // 3])
                    motor.set_param("Curve ID", 1)

        self.model.mark_unprepared()
        actual = waves.achievable_period(height, period)
        message = (
            "{0} sent to {1} piston(s): {2} mm stroke, {3} mm/s, about "
            "{4:.1f} s a cycle.".format(
                self.shape.name, count, height, values["Speed 1"], actual
            )
        )
        how = (
            "  Go to Operate, choose Curve and press Start -- a travelling wave "
            "needs the curve feature, because the per-column timing lives there."
            if travelling else
            "  Go to Operate and press Start."
        )
        self.result.configure(text=message + how)
        self.view.status(message)
        self.logger.info("Wave applied - %s", message)
        self.view.refresh_all()
