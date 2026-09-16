"""Operator-facing directions must follow piston 1 at the front left."""

from types import SimpleNamespace

import pytest

from app import patterns, tags
from modules.tank_view import TankView, describe_place
from operate.Operate import Operate
from operate.PatternDialog import PatternDialog


class RecordingCanvas:
    def __init__(self):
        self.texts = []
        self.lines = []
        self.rectangles = []

    def delete(self, *args):
        pass

    def winfo_width(self):
        return 900

    def create_text(self, *args, **kwargs):
        self.texts.append((args, kwargs))

    def create_line(self, *args, **kwargs):
        self.lines.append((args, kwargs))

    def create_rectangle(self, *args, **kwargs):
        self.rectangles.append((args, kwargs))


def test_front_and_back_descriptions_match_drawing():
    view = TankView.__new__(TankView)
    metrics = dict(pad_x=0, pad_top=0, cell_w=10, cell_h=10)
    assert view._cell_centre(29, metrics) == (5, 5)
    assert view._cell_centre(27, metrics) == (5, 25)
    assert view._cell_centre(0, metrics) == (95, 25)
    assert describe_place(29) == "top, front (nearest you)"
    assert describe_place(28) == "middle, front (nearest you)"
    assert describe_place(27) == "bottom, front (nearest you)"
    assert describe_place(26) == "top, column 2 of 10"
    assert describe_place(0) == "bottom, back of chamber"


def test_ruler_counts_from_front_left_towards_back_right():
    view = TankView.__new__(TankView)
    view.canvas = RecordingCanvas()
    view._draw_ruler(dict(pad_x=10, cell_w=80, height=260, width=820))
    texts = view.canvas.texts
    assert [item[1]["text"] for item in texts[:10]] == list(map(str, range(1, 11)))
    assert texts[10][1]["text"] == "FRONT - nearest you"
    assert texts[10][0][0] < texts[11][0][0]
    assert view.canvas.lines[0][1]["arrow"] == "last"


@pytest.mark.parametrize("columns", [1, 4, 10])
def test_depth_selection_starts_at_piston_one(model, columns):
    view = Operate.__new__(Operate)
    view.model = model
    view.depth_var = SimpleNamespace(get=lambda: str(columns))
    view._live_group = lambda: None
    view.refresh = lambda state: None
    view._say = lambda message: None
    view.select_by_depth()
    assert sorted(tags.display_number(a) for a in model.selected_axes()) == list(
        range(1, columns * 3 + 1)
    )


@pytest.mark.parametrize("direction, axes", [
    (patterns.ACROSS_COLUMNS, [29, 26, 23]),
    (patterns.ACROSS_ROWS, [29, 28, 27]),
    (patterns.ACROSS_SELECTION, [29, 25, 0]),
])
def test_pattern_direction_starts_at_first_displayed_position(direction, axes):
    result = patterns.build(
        list(reversed(axes)), "Curve Offset", patterns.STAGGER,
        start=10, step=5, across=direction,
    )
    assert [result.values[a] for a in axes] == [10, 15, 20]


def test_pattern_preview_labels_and_values_follow_display_order():
    dialog = PatternDialog.__new__(PatternDialog)
    dialog.preview = RecordingCanvas()
    dialog.result = patterns.PatternResult({0: 30, 29: 10, 28: 20}, [])
    dialog._draw_preview()
    assert [item[1]["text"] for item in dialog.preview.texts[:3]] == ["1", "2", "30"]
    bars = dialog.preview.rectangles
    assert bars[0][0][1] > bars[1][0][1] > bars[2][0][1]


def test_wave_preview_offsets_follow_front_to_back_columns():
    view = Operate.__new__(Operate)
    motors = []
    for axis, offset in ((29, 100), (26, 200), (0, 900)):
        motors.append(SimpleNamespace(axis=axis, write_params={
            "Position 1": 0, "Position 2": 100,
            "Speed 1": 100, "Speed 2": 100, "Curve Offset": offset,
        }))
    # cascade_targets is consulted for where each column starts; a model
    # without one must not break the picture.
    view.model = SimpleNamespace(all_motors=motors, cascade_targets=dict)
    calls = []
    view.wave_preview = SimpleNamespace(
        show=lambda *args, **kwargs: calls.append((args, kwargs))
    )
    view._refresh_wave_preview()
    args, kwargs = calls[0]
    assert args[3] == {0: 0.1, 1: 0.2, 9: 0.9}
    # The speeds and dwells reach the strip too, or it cannot draw the
    # fast-up-slow-down asymmetry or the flats at the ends of the stroke.
    assert kwargs["speed_1"] == 100
    assert kwargs["speed_2"] == 100
    assert kwargs["dwell_1_s"] == 0.0
    assert kwargs["dwell_2_s"] == 0.0
