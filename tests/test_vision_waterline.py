"""The surface is read where it was drawn, through clutter and either way round.

These run against a drawn tank.  They show the reading is right when the
surface is a clean edge; they say nothing about glare or a meniscus.
"""

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from vision.calibration import Rig, locate  # noqa: E402
from vision.synthetic import SyntheticTank  # noqa: E402
from vision.waterline import WaterlineReader  # noqa: E402


def _reader(tank, rig=None, depth=True):
    rig = rig or tank.rig
    reader = WaterlineReader(rig, locate(tank.frame(0.0), rig))
    reader.learn_still([tank.frame(0.0) for _ in range(5)],
                       near_depth_mm=tank.depth_mm if depth else None)
    return reader


@pytest.mark.parametrize("water_is_darker", [True, False])
def test_still_water_is_read_at_its_depth(water_is_darker):
    tank = SyntheticTank(water_is_darker=water_is_darker)
    reader = _reader(tank)
    assert np.nanmax(np.abs(reader.still_mm - tank.depth_mm)) < 0.5
    assert reader._polarity == (-1 if water_is_darker else 1)


@pytest.mark.parametrize("water_is_darker", [True, False])
def test_a_passing_wave_is_followed_along_the_whole_view(water_is_darker):
    tank = SyntheticTank(water_is_darker=water_is_darker)
    reader = _reader(tank)
    for moment in (1.3, 1.9, 2.4):
        heights = reader.read(tank.frame(moment))
        truth = tank.depth_mm + tank.rise_mm(reader.x_mm, moment)
        assert np.nanmax(np.abs(heights - truth)) < 1.0
        assert np.sqrt(np.nanmean((heights - truth) ** 2)) < 0.25


def test_a_post_in_front_of_the_surface_is_a_gap_not_a_guess():
    tank = SyntheticTank()
    reader = _reader(tank)
    behind_post = np.abs(reader.x_mm - 480.0) < 6.0
    assert np.isnan(reader.still_mm[behind_post]).all()
    assert np.isfinite(reader.still_mm[np.abs(reader.x_mm - 480.0) > 30.0]).all()


def test_the_depth_hint_keeps_the_reader_off_a_sharper_rail():
    """The rail is a crisper level edge than the water and sits in the strip."""
    tank = SyntheticTank()
    fooled = _reader(tank, depth=False)
    assert np.nanmedian(fooled.still_mm) == pytest.approx(tank.depth_mm + 74.0, abs=3.0)
    assert np.nanmedian(_reader(tank).still_mm) == pytest.approx(tank.depth_mm, abs=0.3)


def test_a_depth_outside_the_strip_is_refused():
    tank = SyntheticTank()
    reader = WaterlineReader(tank.rig, locate(tank.frame(0.0), tank.rig))
    with pytest.raises(ValueError, match="outside the strip"):
        reader.learn_still([tank.frame(0.0)], near_depth_mm=900.0)


def test_a_crest_above_the_strip_is_a_gap_not_the_strip_edge():
    tank = SyntheticTank()
    low = Rig(tank.rig.markers_mm, tank.rig.x_range_mm,
              (tank.depth_mm - 60.0, tank.depth_mm + 20.0))
    reader = _reader(tank, low)
    moment = tank.crest_time_s(700.0)
    heights = reader.read(tank.frame(moment))
    under_crest = np.abs(reader.x_mm - 700.0) < 100.0
    assert np.isnan(heights[under_crest]).all()


def test_rise_needs_still_water_first():
    tank = SyntheticTank()
    reader = WaterlineReader(tank.rig, locate(tank.frame(0.0), tank.rig))
    with pytest.raises(RuntimeError):
        reader.rise(tank.frame(0.0))


def test_glass_correction_scales_rise_and_distance_together():
    tank = SyntheticTank()
    thick = Rig(tank.rig.markers_mm, tank.rig.x_range_mm, tank.rig.z_range_mm,
                glass_thickness_mm=30.0, camera_distance_mm=1000.0)
    plain, corrected = _reader(tank), _reader(tank, thick)
    moment = tank.crest_time_s(700.0)
    ratio = (np.nanmax(corrected.rise(tank.frame(moment))) /
             np.nanmax(plain.rise(tank.frame(moment))))
    assert ratio == pytest.approx(1.02, abs=0.005)
    assert np.ptp(corrected.x_mm) / np.ptp(plain.x_mm) == pytest.approx(1.02)


def test_the_drawn_outline_lands_on_the_surface_in_the_picture():
    tank = SyntheticTank()
    reader = _reader(tank)
    outline = reader.outline_px(reader.still_mm)
    shown = reader.x_mm[np.isfinite(reader.still_mm)]
    for place in (0, len(outline) // 2, len(outline) - 1):
        assert outline[place] == pytest.approx(
            tank.to_pixels(shown[place], tank.depth_mm), abs=0.5)
