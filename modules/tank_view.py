"""A drawing of the piston array, used for both selecting and watching.

Thirty pistons in three rows of ten, over the water. Each piston shows the
stroke it has been given and, while the machine runs, where it actually is.

This replaces two separate widgets that did not agree with each other: a row of
thirty checkboxes on Define Motors and a grid of plain circles on Control Home.
Selecting a piston in one place and looking for it in the other meant counting
along a row of identical labels.

Everything is drawn on a plain ``tkinter.Canvas``. The lab PC is offline
Windows 7, so no drawing library can be installed -- there is no PIL, no
matplotlib, nothing but what ships with Python.

Orientation
-----------
``axis`` 0..29. Row is ``axis % 3 + 1`` and column is ``axis // 3 + 1``, which
is how ``Motor`` has always mapped them: axes 0, 1, 2 are the three rows of
column 1.

Physically, columns run **front to back** into the chamber -- column 1 is
nearest the operator, column 10 is at the back -- and rows run top to bottom.
So piston 27 (row 1, column 10) is the top back corner and piston 2 (row 3,
column 1) is the front corner nearest you. The drawing is laid out to match,
with column 1 on the left.

That makes the column direction the one a wave travels along, which is why the
pattern tool calls a stagger across columns a "front to back" stagger.
"""

from __future__ import annotations

from tkinter import Canvas
from typing import Callable, Dict, List, Optional, Tuple

ROWS = 3
COLUMNS = 10
MOTOR_COUNT = ROWS * COLUMNS

# --- Colours -----------------------------------------------------------------

WATER_TOP = "#1b3a4d"
WATER_BOTTOM = "#0d2030"
SKY = "#141416"
TRACK = "#2c2c2e"
TRACK_EDGE = "#3a3a3c"

FREE_FILL = "#4a4a4e"
FREE_EDGE = "#5a5a5e"
SELECTED_EDGE = "#0a84ff"
LABEL = "#c8c8cc"
LABEL_DIM = "#6e6e73"
GRIP = "#f5f5f7"

#: Fill per group, cycled. Apple's system colours, which stay distinguishable
#: side by side and against the water.
SET_COLOURS: List[str] = [
    "#30d158",  # green
    "#0a84ff",  # blue
    "#ff9f0a",  # orange
    "#bf5af2",  # purple
    "#ff375f",  # pink
    "#ffd60a",  # yellow
]

POSITION_MIN = -20
POSITION_MAX = 370


def describe_place(axis: int) -> str:
    """Where a piston is, in the chamber's own terms.

    "row 1, column 10" says nothing on its own; "top, back of chamber" is what
    the operator is actually looking at.
    """
    row = axis % ROWS
    column = axis // ROWS
    vertical = ("top", "middle", "bottom")[row]
    if column == 0:
        depth = "front (nearest you)"
    elif column == COLUMNS - 1:
        depth = "back of chamber"
    else:
        depth = "column {0} of {1}".format(column + 1, COLUMNS)
    return "{0}, {1}".format(vertical, depth)


class TankView:
    """The piston array, drawn to fit whatever space it is given.

    Callbacks
    ---------
    ``on_select(axes, additive)``   a click or drag chose these axes.
    ``on_hover(axis)``              the pointer moved onto a piston, or None.
    ``on_stroke(axis, low, high)``  a stroke bar was dragged to a new range.
    """

    def __init__(
        self,
        parent,
        on_select: Optional[Callable[[List[int], bool], None]] = None,
        on_hover: Optional[Callable[[Optional[int]], None]] = None,
        on_stroke: Optional[Callable[[int, int, int], None]] = None,
        height: int = 260,
        interactive: bool = True,
    ) -> None:
        self.on_select = on_select
        self.on_hover = on_hover
        #: Dragging the bar above a piston sets how far that piston travels.
        #: Far more obvious than typing two numbers into boxes, and it is the
        #: only place the stroke of a single piston can be set at all.
        self.on_stroke = on_stroke
        self.interactive = interactive

        self.canvas = Canvas(
            parent,
            height=height,
            background=SKY,
            highlightthickness=0,
            bd=0,
        )

        # -- state the caller sets --------------------------------------------
        #: axis -> index of the set it belongs to, for colouring.
        self.set_of: Dict[int, int] = {}
        #: axis -> label of the set, for the tooltip line.
        self.set_name: Dict[int, str] = {}
        #: axes ticked but not yet grouped.
        self.selected: set = set()
        #: axis -> (position 1, position 2) in mm, the commanded stroke.
        self.strokes: Dict[int, Tuple[int, int]] = {}
        #: axis -> actual position in mm, while running.
        self.positions: Dict[int, float] = {}
        #: axes that could not be read or reported a fault.
        self.faulted: set = set()
        self.live = False

        # -- internal ---------------------------------------------------------
        self._boxes: Dict[int, Tuple[float, float, float, float]] = {}
        self._drag_from: Optional[Tuple[float, float]] = None
        self._drag_rect = None
        self._hovered: Optional[int] = None

        # Dragging a stroke bar.
        self._tracks: Dict[int, Tuple[float, float, float, float]] = {}
        self._stroke_axis: Optional[int] = None
        self._stroke_grip: Optional[str] = None
        self._stroke_origin: Optional[Tuple[int, int, float]] = None

        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        if interactive:
            self.canvas.bind("<Button-1>", self._on_press)
            self.canvas.bind("<B1-Motion>", self._on_drag)
            self.canvas.bind("<ButtonRelease-1>", self._on_release)
            self.canvas.bind("<Motion>", self._on_motion)
            self.canvas.bind("<Leave>", lambda _e: self._set_hover(None))

    # -- placement ------------------------------------------------------------

    def grid(self, **kwargs):
        self.canvas.grid(**kwargs)
        return self

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self

    # -- state ----------------------------------------------------------------

    def show_sets(self, sets) -> None:
        """Colour pistons by the set they belong to. ``sets`` is a list of
        objects with ``.name`` and ``.axes``."""
        self.set_of = {}
        self.set_name = {}
        for index, motor_set in enumerate(sets):
            for axis in motor_set.axes:
                self.set_of[axis] = index
                self.set_name[axis] = motor_set.name
        self.redraw()

    def show_selection(self, axes) -> None:
        self.selected = set(axes)
        self.redraw()

    def show_strokes(self, strokes: Dict[int, Tuple[int, int]]) -> None:
        self.strokes = dict(strokes)
        self.redraw()

    def show_positions(self, positions: Dict[int, float]) -> None:
        """Update the live position markers. Called several times a second."""
        self.positions = dict(positions)
        self.live = True
        self._draw_markers()

    def clear_positions(self) -> None:
        """Stop showing live positions, and forget any warnings with them.

        Faults used to persist here: nothing watches the pistons once a run
        ends, so a piston flagged during the run stayed red indefinitely and
        looked like a live fault long after it had been dealt with.
        """
        self.positions = {}
        self.faulted = set()
        self.live = False
        self.redraw()

    def show_faults(self, axes) -> None:
        self.faulted = set(axes)
        self.redraw()

    def colour_for(self, axis: int) -> str:
        index = self.set_of.get(axis)
        if index is None:
            return FREE_FILL
        return SET_COLOURS[index % len(SET_COLOURS)]

    # -- geometry -------------------------------------------------------------

    def _metrics(self):
        """Work out the drawing geometry for the current canvas size."""
        width = max(self.canvas.winfo_width(), 320)
        height = max(self.canvas.winfo_height(), 140)

        pad_x = 14
        pad_top = 22          # room for the column ruler
        pad_bottom = 20       # room for the front/back labels

        usable_w = width - pad_x * 2
        usable_h = height - pad_top - pad_bottom

        cell_w = usable_w / float(COLUMNS)
        cell_h = usable_h / float(ROWS)

        # A paddle, not a column. Capped in both directions so that on a large
        # display the pistons stay a sensible size and the water still reads as
        # water, rather than the boxes stretching to fill the whole cell.
        paddle_w = max(min(cell_w * 0.56, 62.0), 14.0)
        paddle_h = max(min(cell_h * 0.54, 86.0), 14.0)

        return {
            "width": width,
            "height": height,
            "pad_x": pad_x,
            "pad_top": pad_top,
            "cell_w": cell_w,
            "cell_h": cell_h,
            "paddle_w": paddle_w,
            "paddle_h": paddle_h,
        }

    def _cell_centre(self, axis: int, m) -> Tuple[float, float]:
        row = axis % ROWS
        column = axis // ROWS
        cx = m["pad_x"] + (column + 0.5) * m["cell_w"]
        cy = m["pad_top"] + (row + 0.5) * m["cell_h"]
        return cx, cy

    # -- drawing --------------------------------------------------------------

    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        m = self._metrics()
        self._boxes = {}
        self._tracks = {}

        self._draw_water(m)
        self._draw_ruler(m)

        for axis in range(MOTOR_COUNT):
            self._draw_piston(axis, m)

        self._draw_markers()

    def _draw_water(self, m) -> None:
        """A few horizontal bands standing in for the tank. Purely so the
        drawing reads as pistons over water rather than an abstract grid."""
        c = self.canvas
        top = m["pad_top"] - 6
        bottom = m["height"]
        bands = 14
        for i in range(bands):
            y0 = top + (bottom - top) * i / float(bands)
            y1 = top + (bottom - top) * (i + 1) / float(bands)
            shade = self._blend(WATER_TOP, WATER_BOTTOM, i / float(bands - 1))
            c.create_rectangle(0, y0, m["width"], y1, fill=shade, outline=shade)

    @staticmethod
    def _blend(start: str, end: str, t: float) -> str:
        t = min(max(t, 0.0), 1.0)
        s = tuple(int(start[i : i + 2], 16) for i in (1, 3, 5))
        e = tuple(int(end[i : i + 2], 16) for i in (1, 3, 5))
        mixed = tuple(int(round(s[i] + (e[i] - s[i]) * t)) for i in range(3))
        return "#%02x%02x%02x" % mixed

    def _draw_ruler(self, m) -> None:
        """Column numbers and which way the chamber runs.

        Without this the picture is just a grid, and there is nothing to tell
        you that the right-hand side is the back of the chamber.
        """
        c = self.canvas
        for column in range(COLUMNS):
            x = m["pad_x"] + (column + 0.5) * m["cell_w"]
            c.create_text(
                x, 10, text=str(column + 1), fill=LABEL_DIM,
                font=("Segoe UI", 8),
            )

        baseline = m["height"] - 7
        c.create_text(
            m["pad_x"], baseline, text="FRONT - nearest you", anchor="w",
            fill=LABEL_DIM, font=("Segoe UI", 8),
        )
        c.create_text(
            m["width"] - m["pad_x"], baseline, text="BACK of chamber", anchor="e",
            fill=LABEL_DIM, font=("Segoe UI", 8),
        )

        # A thin arrow between the two labels, so the direction reads at a glance.
        mid = m["width"] / 2.0
        c.create_line(
            mid - 62, baseline, mid + 62, baseline,
            fill="#3f4a56", width=1, arrow="last", arrowshape=(7, 9, 3),
        )

    def _draw_piston(self, axis: int, m) -> None:
        c = self.canvas
        cx, cy = self._cell_centre(axis, m)
        half_w = m["paddle_w"] / 2.0
        half_h = m["paddle_h"] / 2.0

        # The stroke track: the span this piston has been told to travel.
        track_w = m["cell_w"] * 0.80
        tx0 = cx - track_w / 2.0
        tx1 = cx + track_w / 2.0
        self._tracks[axis] = (tx0, cy - half_h - 11, tx1, cy - half_h + 2)
        c.create_rectangle(
            tx0, cy - half_h - 6, tx1, cy - half_h - 1,
            fill=TRACK, outline=TRACK_EDGE,
        )

        stroke = self.strokes.get(axis)
        if stroke is not None:
            a, b = sorted(stroke)
            x0 = tx0 + (tx1 - tx0) * self._fraction(a)
            x1 = tx0 + (tx1 - tx0) * self._fraction(b)
            if x1 - x0 < 2:
                x1 = x0 + 2
            c.create_rectangle(
                x0, cy - half_h - 6, x1, cy - half_h - 1,
                fill=self.colour_for(axis), outline="",
            )
            # Grips, so the bar visibly reads as something you can pull.
            if self.on_stroke is not None and axis in self.set_of:
                for x in (x0, x1):
                    c.create_rectangle(
                        x - 2, cy - half_h - 10, x + 2, cy - half_h + 2,
                        fill=GRIP, outline="",
                    )

        # The paddle itself.
        fill = self.colour_for(axis)
        if axis in self.faulted:
            # A piston that is not following its demand, or cannot be read.
            fill = "#ff453a"
        outline = FREE_EDGE
        width = 1
        if axis in self.selected:
            outline = SELECTED_EDGE
            width = 2

        box = (cx - half_w, cy - half_h, cx + half_w, cy + half_h)
        self._boxes[axis] = box
        c.create_rectangle(*box, fill=fill, outline=outline, width=width)

        # Axis number, dark on light fills and light on dark ones.
        c.create_text(
            cx, cy,
            text=str(axis),
            fill="#1c2530" if axis in self.set_of else LABEL,
            font=("Segoe UI", 8, "bold" if axis in self.set_of else "normal"),
        )

    def _fraction(self, position: float) -> float:
        span = float(POSITION_MAX - POSITION_MIN)
        return min(max((position - POSITION_MIN) / span, 0.0), 1.0)

    def _draw_markers(self) -> None:
        """Draw just the live position markers.

        Separated from :meth:`redraw` because it runs several times a second
        while the machine moves; redrawing the whole tank that often flickers.
        """
        c = self.canvas
        c.delete("marker")
        if not self.live or not self._boxes:
            return
        m = self._metrics()
        for axis, position in self.positions.items():
            box = self._boxes.get(axis)
            if box is None:
                continue
            cx, cy = self._cell_centre(axis, m)
            half_h = m["paddle_h"] / 2.0
            track_w = m["cell_w"] * 0.80
            tx0 = cx - track_w / 2.0
            x = tx0 + track_w * self._fraction(position)
            c.create_line(
                x, cy - half_h - 8, x, cy - half_h + 1,
                fill="#ffffff", width=2, tags="marker",
            )

    # -- interaction ----------------------------------------------------------

    def _axis_at(self, x: float, y: float) -> Optional[int]:
        for axis, (x0, y0, x1, y1) in self._boxes.items():
            if x0 <= x <= x1 and y0 <= y <= y1:
                return axis
        return None

    # -- dragging a stroke bar ------------------------------------------------

    def _track_at(self, x: float, y: float):
        """Which stroke bar, and which part of it, is under the pointer."""
        if self.on_stroke is None:
            return None
        for axis, (tx0, ty0, tx1, ty1) in self._tracks.items():
            if not (ty0 <= y <= ty1 and tx0 - 8 <= x <= tx1 + 8):
                continue
            if axis not in self.set_of or axis not in self.strokes:
                return None  # only a piston in a group has a stroke to set
            low, high = sorted(self.strokes[axis])
            xlow = tx0 + (tx1 - tx0) * self._fraction(low)
            xhigh = tx0 + (tx1 - tx0) * self._fraction(high)
            if abs(x - xlow) <= 8:
                return axis, "low"
            if abs(x - xhigh) <= 8:
                return axis, "high"
            if xlow < x < xhigh:
                return axis, "whole"
            return axis, "low" if x < xlow else "high"
        return None

    def _position_at(self, axis: int, x: float) -> int:
        tx0, _ty0, tx1, _ty1 = self._tracks[axis]
        fraction = (x - tx0) / max(tx1 - tx0, 1.0)
        fraction = min(max(fraction, 0.0), 1.0)
        return int(round(POSITION_MIN + fraction * (POSITION_MAX - POSITION_MIN)))

    def _drag_stroke(self, event) -> None:
        axis = self._stroke_axis
        low, high, start_x = self._stroke_origin
        here = self._position_at(axis, event.x)

        if self._stroke_grip == "low":
            low = min(here, high - 1)
        elif self._stroke_grip == "high":
            high = max(here, low + 1)
        else:
            shift = here - self._position_at(axis, start_x)
            span = high - low
            low = min(max(low + shift, POSITION_MIN), POSITION_MAX - span)
            high = low + span

        low = max(POSITION_MIN, min(low, POSITION_MAX))
        high = max(POSITION_MIN, min(high, POSITION_MAX))
        self.strokes[axis] = (low, high)
        self.redraw()
        self._show_stroke_label(axis, low, high)

    def _show_stroke_label(self, axis: int, low: int, high: int) -> None:
        m = self._metrics()
        cx, cy = self._cell_centre(axis, m)
        self.canvas.delete("strokelabel")
        self.canvas.create_text(
            cx, cy - m["paddle_h"] / 2.0 - 22,
            text="{0} - {1} mm".format(low, high),
            fill=GRIP, font=("Segoe UI", 8, "bold"), tags="strokelabel",
        )

    # -- selecting ------------------------------------------------------------

    def _on_press(self, event) -> None:
        grabbed = self._track_at(event.x, event.y)
        if grabbed is not None:
            axis, grip = grabbed
            low, high = sorted(self.strokes[axis])
            self._stroke_axis = axis
            self._stroke_grip = grip
            self._stroke_origin = (low, high, event.x)
            self.canvas.configure(cursor="sb_h_double_arrow")
            return
        self._drag_from = (event.x, event.y)

    def _on_drag(self, event) -> None:
        if self._stroke_axis is not None:
            self._drag_stroke(event)
            return
        if self._drag_from is None:
            return
        x0, y0 = self._drag_from
        if self._drag_rect is not None:
            self.canvas.delete(self._drag_rect)
        self._drag_rect = self.canvas.create_rectangle(
            x0, y0, event.x, event.y, outline=SELECTED_EDGE, dash=(3, 2)
        )

    def _on_release(self, event) -> None:
        if self._stroke_axis is not None:
            axis = self._stroke_axis
            low, high = self.strokes[axis]
            self._stroke_axis = None
            self._stroke_grip = None
            self._stroke_origin = None
            self.canvas.configure(cursor="")
            self.canvas.delete("strokelabel")
            if self.on_stroke is not None:
                self.on_stroke(axis, low, high)
            return
        if self._drag_from is None:
            return
        x0, y0 = self._drag_from
        self._drag_from = None
        if self._drag_rect is not None:
            self.canvas.delete(self._drag_rect)
            self._drag_rect = None

        if self.on_select is None:
            return

        moved = abs(event.x - x0) > 4 or abs(event.y - y0) > 4
        additive = bool(event.state & 0x0001) or bool(event.state & 0x0004)

        if not moved:
            axis = self._axis_at(event.x, event.y)
            if axis is not None:
                self.on_select([axis], True)  # a click always toggles
            return

        left, right = sorted((x0, event.x))
        top, bottom = sorted((y0, event.y))
        caught = [
            axis
            for axis, (bx0, by0, bx1, by1) in self._boxes.items()
            if left < (bx0 + bx1) / 2 < right and top < (by0 + by1) / 2 < bottom
        ]
        if caught:
            self.on_select(sorted(caught), additive)

    def _on_motion(self, event) -> None:
        if self._stroke_axis is None:
            over = self._track_at(event.x, event.y)
            self.canvas.configure(
                cursor="sb_h_double_arrow" if over is not None else ""
            )
        self._set_hover(self._axis_at(event.x, event.y))

    def _set_hover(self, axis: Optional[int]) -> None:
        if axis == self._hovered:
            return
        self._hovered = axis
        if self.on_hover is not None:
            self.on_hover(axis)
