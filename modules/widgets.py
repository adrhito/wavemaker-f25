"""Controls drawn by hand, because ttk on Windows 7 cannot do them.

The lab PC is offline Windows 7. There is no ttkbootstrap, no CustomTkinter, no
PIL -- only what ships with Python. ttk on that platform also ignores
``background`` on buttons and has no notion of a corner radius, so a button
that looks like anything other than a 2001 Win32 button has to be drawn.

Everything here is a ``tkinter.Canvas`` with shapes on it. That costs nothing at
run time and looks the same on Windows 7 as on 11.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

from style import theme


def rounded_rect(canvas: tk.Canvas, x0, y0, x1, y1, radius, **kwargs):
    """A rectangle with round corners, as a single filled polygon.

    Tk has no rounded rectangle. Drawing arcs and lines separately leaves seams
    at the joins, so this traces the outline as one smooth polygon instead.
    """
    radius = max(0, min(radius, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    points = [
        x0 + radius, y0,
        x1 - radius, y0,
        x1, y0,
        x1, y0 + radius,
        x1, y1 - radius,
        x1, y1,
        x1 - radius, y1,
        x0 + radius, y1,
        x0, y1,
        x0, y1 - radius,
        x0, y0 + radius,
        x0, y0,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


class RoundedButton:
    """A flat, rounded, coloured button.

    Variants: ``primary`` (filled accent), ``secondary`` (quiet fill),
    ``danger`` (filled red), ``ghost`` (text only until hovered).
    """

    HEIGHTS = {"large": 46, "normal": 34, "small": 28}
    FONTS = {
        "large": ("Segoe UI Semibold", 13),
        "normal": ("Segoe UI", 10),
        "small": ("Segoe UI", 9),
    }
    RADII = {"large": 12, "normal": 8, "small": 7}

    def __init__(
        self,
        parent,
        text: str,
        command: Optional[Callable[[], None]] = None,
        variant: str = "secondary",
        size: str = "normal",
        width: int = 150,
    ) -> None:
        self.text = text
        self.command = command
        self.variant = variant
        self.size = size
        self._state = "normal"
        self._hovered = False

        height = self.HEIGHTS[size]
        self.canvas = tk.Canvas(
            parent,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            background=theme.BACKGROUND,
            cursor="hand2",
        )
        self._width = width
        self._height = height

        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Configure>", self._on_configure)

        self._draw()

    # -- placement ------------------------------------------------------------

    def grid(self, **kwargs):
        self.canvas.grid(**kwargs)
        return self

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self

    def grid_remove(self):
        self.canvas.grid_remove()

    # -- appearance -----------------------------------------------------------

    def set_background(self, colour: str) -> None:
        """Match the button to whatever it is sitting on."""
        self.canvas.configure(background=colour)
        self._draw()

    def configure_text(self, text: str) -> None:
        self.text = text
        self._draw()

    def set_state(self, state: str) -> None:
        """``normal`` or ``disabled``."""
        self._state = state
        self.canvas.configure(cursor="hand2" if state == "normal" else "arrow")
        self._draw()

    def __setitem__(self, key, value):
        # So callers can keep using button['state'] = 'disabled' like a ttk one.
        if key == "state":
            self.set_state(value)
        elif key == "text":
            self.configure_text(value)

    def __getitem__(self, key):
        if key == "state":
            return self._state
        if key == "text":
            return self.text
        raise KeyError(key)

    def _colours(self):
        disabled = self._state == "disabled"
        if self.variant == "primary":
            fill = theme.ACCENT_DIM if disabled else (
                theme.ACCENT_HOVER if self._hovered else theme.ACCENT
            )
            text = theme.LABEL_DISABLED if disabled else "#ffffff"
        elif self.variant == "danger":
            fill = theme.DANGER_DIM if disabled else (
                theme.DANGER_HOVER if self._hovered else theme.DANGER
            )
            text = theme.LABEL_DISABLED if disabled else "#ffffff"
        elif self.variant == "ghost":
            fill = theme.SURFACE_2 if (self._hovered and not disabled) else theme.BACKGROUND
            text = theme.LABEL_DISABLED if disabled else theme.LABEL_SECONDARY
        else:
            fill = theme.SURFACE_3 if (self._hovered and not disabled) else theme.SURFACE_2
            text = theme.LABEL_DISABLED if disabled else theme.LABEL
        return fill, text

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        width = self._width
        height = self._height
        fill, text_colour = self._colours()

        rounded_rect(c, 1, 1, width - 1, height - 1, self.RADII[self.size], fill=fill, outline="")
        c.create_text(
            width / 2.0, height / 2.0,
            text=self.text, fill=text_colour, font=self.FONTS[self.size],
        )

    def _on_configure(self, event) -> None:
        if event.width != self._width or event.height != self._height:
            self._width, self._height = event.width, event.height
            self._draw()

    # -- events ---------------------------------------------------------------

    def _on_enter(self, _event) -> None:
        self._hovered = True
        self._draw()

    def _on_leave(self, _event) -> None:
        self._hovered = False
        self._draw()

    def _on_press(self, _event) -> None:
        if self._state == "disabled":
            return
        self.canvas.move("all", 0, 1)

    def _on_release(self, event) -> None:
        if self._state == "disabled":
            return
        self._draw()
        inside = 0 <= event.x <= self._width and 0 <= event.y <= self._height
        if inside and self.command is not None:
            self.command()


class Segmented:
    """An iOS-style segmented control: a few exclusive options in one pill.

    Replaces a pair of radio buttons, which on Windows 7 ttk render as small
    grey circles that are easy to miss entirely.
    """

    def __init__(self, parent, options, command=None, width=230, height=32):
        self.options = list(options)          # [(value, label), ...]
        self.command = command
        self.value = self.options[0][0]
        self._width = width
        self._height = height
        self._state = "normal"

        self.canvas = tk.Canvas(
            parent, width=width, height=height,
            highlightthickness=0, bd=0,
            background=theme.BACKGROUND, cursor="hand2",
        )
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Configure>", self._on_configure)
        self._draw()

    def grid(self, **kwargs):
        self.canvas.grid(**kwargs)
        return self

    def set_state(self, state: str) -> None:
        self._state = state
        self.canvas.configure(cursor="hand2" if state == "normal" else "arrow")
        self._draw()

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value
        self._draw()

    def _segment_width(self):
        return float(self._width) / len(self.options)

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        rounded_rect(c, 0, 0, self._width, self._height, 9,
                     fill=theme.SURFACE_2, outline="")

        seg = self._segment_width()
        disabled = self._state == "disabled"
        for index, (value, label) in enumerate(self.options):
            x0 = index * seg
            if value == self.value:
                rounded_rect(
                    c, x0 + 2, 2, x0 + seg - 2, self._height - 2, 7,
                    fill=theme.SURFACE_SELECTED if not disabled else theme.SURFACE_3,
                    outline="",
                )
            colour = theme.LABEL_DISABLED if disabled else (
                theme.LABEL if value == self.value else theme.LABEL_SECONDARY
            )
            c.create_text(
                x0 + seg / 2.0, self._height / 2.0,
                text=label, fill=colour, font=("Segoe UI", 9),
            )

    def _on_configure(self, event) -> None:
        if event.width != self._width:
            self._width = event.width
            self._draw()

    def _on_click(self, event) -> None:
        if self._state == "disabled":
            return
        index = int(event.x // self._segment_width())
        index = max(0, min(index, len(self.options) - 1))
        value = self.options[index][0]
        if value != self.value:
            self.value = value
            self._draw()
            if self.command is not None:
                self.command(value)
