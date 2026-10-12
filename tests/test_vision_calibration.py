"""Markers on the wall turn pixels into millimetres, and say when they cannot."""

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from vision.calibration import (  # noqa: E402
    CalibrationError, Rig, find_markers, locate,
)
from vision.synthetic import SyntheticTank  # noqa: E402


@pytest.fixture(scope="module")
def tank():
    return SyntheticTank()


def test_every_marker_is_found_where_it_was_drawn(tank):
    found = find_markers(tank.frame(0.0))
    assert sorted(found) == [0, 1, 2, 3, 4, 5]
    for marker, (x_mm, z_mm) in tank.rig.markers_mm.items():
        assert found[marker] == pytest.approx(tank.to_pixels(x_mm, z_mm), abs=0.3)


def test_the_wall_is_located_to_a_fraction_of_a_millimetre(tank):
    view = locate(tank.frame(0.0), tank.rig)
    assert view.marker_error_mm < 0.3
    for point in ((300.0, 150.0), (1250.0, 180.0), (60.0, 95.0)):
        assert view.to_pixels([point])[0] == pytest.approx(tank.to_pixels(*point), abs=0.3)
        assert view.to_wall(view.to_pixels([point]))[0] == pytest.approx(point, abs=1e-6)


def test_a_mismeasured_marker_shows_up_as_error(tank):
    """The spare markers exist to catch a slip of the tape measure."""
    markers = dict(tank.rig.markers_mm)
    markers[1] = (markers[1][0] + 20.0, markers[1][1])
    wrong = Rig(markers, tank.rig.x_range_mm, tank.rig.z_range_mm)
    assert locate(tank.frame(0.0), wrong).marker_error_mm > 3.0


def test_too_few_markers_is_refused_with_what_was_seen(tank):
    frame = tank.frame(0.0)
    frame[:, frame.shape[1] // 3:] = 128
    with pytest.raises(CalibrationError, match="Only 2"):
        locate(frame, tank.rig)


def test_four_markers_fit_exactly_so_no_error_is_claimed(tank):
    four = {m: tank.rig.markers_mm[m] for m in (0, 2, 3, 5)}
    view = locate(tank.frame(0.0), Rig(four, tank.rig.x_range_mm, tank.rig.z_range_mm))
    assert np.isnan(view.marker_error_mm)


def test_a_bumped_tripod_is_noticed(tank):
    frame = tank.frame(0.0)
    before = locate(frame, tank.rig)
    after = locate(np.roll(frame, 7, axis=1), tank.rig)
    assert before.shift_px(before) == 0.0
    assert after.shift_px(before) == pytest.approx(7.0, abs=0.3)


def test_rig_survives_being_saved(tmp_path, tank):
    rig = Rig(tank.rig.markers_mm, tank.rig.x_range_mm, tank.rig.z_range_mm,
              glass_thickness_mm=12.0, camera_distance_mm=2400.0, water_is_darker=True)
    rig.save(tmp_path / "rig.json")
    assert Rig.load(tmp_path / "rig.json") == rig


def test_glass_correction_needs_both_measurements():
    markers = {0: (0, 0), 1: (1, 0), 2: (0, 1), 3: (1, 1)}
    assert Rig(markers, (0, 1), (0, 1)).wall_scale == 1.0
    assert Rig(markers, (0, 1), (0, 1), glass_thickness_mm=12.0).wall_scale == 1.0
    assert Rig(markers, (0, 1), (0, 1), glass_thickness_mm=12.0,
               camera_distance_mm=2000.0).wall_scale == pytest.approx(1.004)


def test_a_rig_needs_enough_markers_and_real_ranges():
    with pytest.raises(ValueError):
        Rig({0: (0, 0), 1: (1, 0), 2: (0, 1)}, (0, 1), (0, 1))
    with pytest.raises(ValueError):
        Rig({0: (0, 0), 1: (1, 0), 2: (0, 1), 3: (1, 1)}, (5, 5), (0, 1))
