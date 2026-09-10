"""The pattern tool: fill one parameter across a set in a shape.

A dialog rather than a panel on the main screen. It needs room for a preview,
and it is used once when building a set rather than constantly, so putting it
on the Define Motors tab would cost permanent space for occasional use.
"""

from __future__ import annotations

from tkinter import StringVar, Toplevel, ttk
from typing import Optional

from app import params, patterns

PREVIEW_BAR_HEIGHT = 74


class PatternDialog:
    """Choose a parameter, a shape, and apply it to a set.

    Shows a live preview of the resulting values as bars across the array, so
    the shape is visible before anything is committed.
    """

    def __init__(self, parent, motor_set, on_apply) -> None:
        self.motor_set = motor_set
        self.on_apply = on_apply
        self.result: Optional[patterns.PatternResult] = None

        self.top = Toplevel(parent)
        self.top.title("Pattern - {0}".format(motor_set.name))
        self.top.configure(bg="black")
        self.top.transient(parent.winfo_toplevel())
        self.top.resizable(True, False)
        self.top.minsize(620, 440)

        frame = ttk.Frame(self.top, padding=18)
        frame.grid(row=0, column=0, sticky="nsew")
        self.top.columnconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        ttk.Label(
            frame,
            text="Fill a parameter across {0}".format(motor_set.name),
            style="Heading2.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            frame,
            text="{0} motors: {1}".format(
                len(motor_set), ", ".join(str(a) for a in motor_set.axes)
            ),
            style="Dim.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 14))

        self._build_controls(frame)
        self._build_preview(frame)
        self._build_buttons(frame)

        self._refresh()

        self.top.grab_set()
        self.top.focus_set()

    # -- construction ---------------------------------------------------------

    def _build_controls(self, parent) -> None:
        box = ttk.Frame(parent)
        box.grid(row=2, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)
        box.columnconfigure(3, weight=1)

        ttk.Label(box, text="Parameter").grid(row=0, column=0, sticky="w", pady=4)
        self.param_var = StringVar(value="Curve Offset")
        self.param_box = ttk.Combobox(
            box,
            textvariable=self.param_var,
            values=params.PARAM_NAMES,
            state="readonly",
            width=20,
        )
        self.param_box.grid(row=0, column=1, sticky="w", padx=(8, 24))

        ttk.Label(box, text="Direction").grid(row=0, column=2, sticky="w", pady=4)
        self.across_var = StringVar(value=patterns.AXES_CHOICES[0][1])
        self.across_box = ttk.Combobox(
            box,
            textvariable=self.across_var,
            values=[label for _key, label in patterns.AXES_CHOICES],
            state="readonly",
            width=24,
        )
        self.across_box.grid(row=0, column=3, sticky="w", padx=(8, 0))

        ttk.Label(box, text="Shape").grid(row=1, column=0, sticky="w", pady=4)
        self.pattern_var = StringVar(value=patterns.PATTERN_CHOICES[2][1])
        self.pattern_box = ttk.Combobox(
            box,
            textvariable=self.pattern_var,
            values=[label for _key, label in patterns.PATTERN_CHOICES],
            state="readonly",
            width=48,
        )
        self.pattern_box.grid(row=1, column=1, columnspan=3, sticky="w", padx=(8, 0))

        self.explain = ttk.Label(parent, text="", wraplength=580, justify="left",
                                 style="Dim.TLabel")
        self.explain.grid(row=3, column=0, sticky="w", pady=(10, 12))

        numbers = ttk.Frame(parent)
        numbers.grid(row=4, column=0, sticky="w")

        self.start_var = StringVar(value="0")
        self.end_var = StringVar(value="0")
        self.step_var = StringVar(value="10")
        self.wrap_var = StringVar(value="")

        self.start_label = ttk.Label(numbers, text="Start")
        self.start_label.grid(row=0, column=0, sticky="e", padx=(0, 6))
        self.start_entry = ttk.Entry(numbers, textvariable=self.start_var, width=9)
        self.start_entry.grid(row=0, column=1, padx=(0, 18))

        self.end_label = ttk.Label(numbers, text="End")
        self.end_label.grid(row=0, column=2, sticky="e", padx=(0, 6))
        self.end_entry = ttk.Entry(numbers, textvariable=self.end_var, width=9)
        self.end_entry.grid(row=0, column=3, padx=(0, 18))

        self.step_label = ttk.Label(numbers, text="Step per column")
        self.step_label.grid(row=0, column=4, sticky="e", padx=(0, 6))
        self.step_entry = ttk.Entry(numbers, textvariable=self.step_var, width=9)
        self.step_entry.grid(row=0, column=5, padx=(0, 18))

        self.wrap_label = ttk.Label(numbers, text="Wrap at")
        self.wrap_label.grid(row=0, column=6, sticky="e", padx=(0, 6))
        self.wrap_entry = ttk.Entry(numbers, textvariable=self.wrap_var, width=9)
        self.wrap_entry.grid(row=0, column=7)

        for var in (
            self.param_var, self.pattern_var, self.across_var,
            self.start_var, self.end_var, self.step_var, self.wrap_var,
        ):
            var.trace_add("write", lambda *_a: self._refresh())

    def _build_preview(self, parent) -> None:
        from tkinter import Canvas

        holder = ttk.Frame(parent)
        holder.grid(row=5, column=0, sticky="ew", pady=(16, 0))
        holder.columnconfigure(0, weight=1)

        ttk.Label(holder, text="Preview", style="Heading2.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.preview = Canvas(
            holder, height=PREVIEW_BAR_HEIGHT, background="#161b21",
            highlightthickness=0, bd=0,
        )
        self.preview.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.preview.bind("<Configure>", lambda _e: self._draw_preview())

        self.summary = ttk.Label(holder, text="", justify="left", wraplength=580)
        self.summary.grid(row=2, column=0, sticky="w", pady=(8, 0))

    def _build_buttons(self, parent) -> None:
        row = ttk.Frame(parent)
        row.grid(row=6, column=0, sticky="e", pady=(18, 0))
        self.apply_button = ttk.Button(row, text="Apply", command=self._apply)
        self.apply_button.grid(row=0, column=0, padx=(0, 8))
        ttk.Button(row, text="Cancel", command=self.top.destroy).grid(row=0, column=1)

    # -- logic ----------------------------------------------------------------

    def _pattern_key(self) -> str:
        label = self.pattern_var.get()
        for key, text in patterns.PATTERN_CHOICES:
            if text == label:
                return key
        return patterns.UNIFORM

    def _across_key(self) -> str:
        label = self.across_var.get()
        for key, text in patterns.AXES_CHOICES:
            if text == label:
                return key
        return patterns.ACROSS_COLUMNS

    @staticmethod
    def _as_int(text: str, fallback: Optional[int] = None) -> Optional[int]:
        try:
            return int(float(text.strip()))
        except (ValueError, AttributeError):
            return fallback

    def _refresh(self) -> None:
        """Recompute the pattern and redraw. Runs on every keystroke."""
        pattern = self._pattern_key()
        self.explain.configure(text=patterns.describe(pattern))

        # Only show the fields this shape actually uses.
        uses_end = pattern in (patterns.RAMP, patterns.MIRROR)
        uses_step = pattern == patterns.STAGGER
        for widget, used in (
            (self.end_label, uses_end), (self.end_entry, uses_end),
            (self.step_label, uses_step), (self.step_entry, uses_step),
            (self.wrap_label, uses_step), (self.wrap_entry, uses_step),
        ):
            if used:
                widget.grid()
            else:
                widget.grid_remove()

        self.start_label.configure(
            text="Centre value" if pattern == patterns.MIRROR else "Start"
        )
        self.end_label.configure(
            text="Edge value" if pattern == patterns.MIRROR else "End"
        )

        param = self.param_var.get()
        spec = params.BY_NAME.get(param)
        if spec is None:
            return

        start = self._as_int(self.start_var.get())
        if start is None:
            self.summary.configure(text="Start must be a whole number.")
            self.result = None
            self.apply_button["state"] = "disabled"
            self.preview.delete("all")
            return

        try:
            self.result = patterns.build(
                self.motor_set.axes,
                param,
                pattern,
                start=start,
                end=self._as_int(self.end_var.get(), start),
                step=self._as_int(self.step_var.get(), 0),
                wrap=self._as_int(self.wrap_var.get()) or None,
                across=self._across_key(),
            )
        except (KeyError, ValueError) as exc:
            self.summary.configure(text=str(exc))
            self.result = None
            self.apply_button["state"] = "disabled"
            return

        self.apply_button["state"] = "normal"
        text = self.result.summary(param)
        text += "\nAccepted range for {0}: {1}.".format(param, spec.describe_range())
        self.summary.configure(text=text)
        self._draw_preview()

    def _draw_preview(self) -> None:
        """Bars showing the value each motor will get, in array order."""
        c = self.preview
        c.delete("all")
        if self.result is None or not self.result.values:
            return

        width = max(c.winfo_width(), 200)
        height = PREVIEW_BAR_HEIGHT
        axes = sorted(self.result.values)
        values = [self.result.values[a] for a in axes]

        low = min(values)
        high = max(values)
        span = float(high - low) if high != low else 1.0

        pad = 8
        usable = width - pad * 2
        bar_w = usable / float(len(axes))

        for index, axis in enumerate(axes):
            value = self.result.values[axis]
            fraction = (value - low) / span
            bar_h = 8 + fraction * (height - 30)
            x0 = pad + index * bar_w + 1
            x1 = pad + (index + 1) * bar_w - 1
            y1 = height - 14
            y0 = y1 - bar_h
            c.create_rectangle(x0, y0, x1, y1, fill="#4fc3f7", outline="")
            if bar_w > 16:
                c.create_text(
                    (x0 + x1) / 2, y1 + 7, text=str(axis),
                    fill="#7d8894", font=("Segoe UI", 7),
                )

        c.create_text(pad + 2, 8, text=str(high), anchor="w",
                      fill="#7d8894", font=("Segoe UI", 7))
        c.create_text(pad + 2, height - 22, text=str(low), anchor="w",
                      fill="#7d8894", font=("Segoe UI", 7))

    def _apply(self) -> None:
        if self.result is None:
            return
        self.on_apply(self.param_var.get(), self.result)
        self.top.destroy()
