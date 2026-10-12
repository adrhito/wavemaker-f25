"""Height, speed and width of a wave from surface readings alone.

The first half needs only NumPy: the measuring is arithmetic on arrays and is
checked against a wave written down as a formula.  The second half goes all
the way from drawn pictures to numbers.
"""

import math

import pytest

np = pytest.importorskip("numpy")

from app.solitons import SolitaryTarget  # noqa: E402
from vision.metrics import (  # noqa: E402
    arrival_times, fill_gaps, measure_pulse, pulse_speed, restlessness_mm,
    station_series,
)


def _soliton(crest=30.0, depth=150.0, rate=30.0, launch=-2000.0, direction=1):
    wave = SolitaryTarget(crest, 1.0, depth)
    wavenumber = math.sqrt(3.0 * crest / (4.0 * depth ** 3))
    places = np.arange(0.0, 1300.0, 5.0)
    times = np.arange(0.0, 4.0, 1.0 / rate)
    crest_at = launch + wave.phase_speed_mm_s * times[:, None]
    rise = crest / np.cosh(wavenumber * (places[None, :] - crest_at)) ** 2
    if direction < 0:
        rise = rise[:, ::-1]
    return wave, times, places, rise


def test_a_written_down_soliton_is_measured_back():
    wave, times, places, rise = _soliton()
    measured = measure_pulse(times, places, rise, still_depth_mm=150.0)
    assert measured.crest_rise_mm == pytest.approx(30.0, abs=0.05)
    assert measured.crest_seen
    assert measured.speed_mm_s == pytest.approx(wave.phase_speed_mm_s, rel=0.002)
    assert measured.fwhm_mm == pytest.approx(wave.theoretical_width_mm, rel=0.01)
    assert measured.direction == 1
    assert measured.shape_misfit < 0.01
    assert measured.theory_fwhm_mm == pytest.approx(wave.theoretical_width_mm, rel=0.01)
    assert measured.trough_mm == pytest.approx(0.0, abs=0.05)


def test_a_wave_travelling_the_other_way_is_the_same_wave():
    wave, times, places, rise = _soliton(direction=-1)
    measured = measure_pulse(times, places, rise)
    assert measured.direction == -1
    assert measured.speed_mm_s == pytest.approx(wave.phase_speed_mm_s, rel=0.002)
    assert measured.theory_fwhm_mm is None


def test_the_crest_between_two_pictures_is_not_clipped():
    """At 10 a second the highest sample can miss the top by several percent."""
    wave, times, places, rise = _soliton(rate=10.0)
    # A station the crest reaches exactly half way between two pictures.
    station = -2000.0 + wave.phase_speed_mm_s * 19.5 / 10.0
    assert np.nanmax(station_series(places, rise, station)) < 29.3
    assert measure_pulse(times, places, rise, station_mm=station).crest_rise_mm == \
        pytest.approx(30.0, abs=0.25)


def test_a_wave_that_is_not_a_soliton_says_so():
    _, times, places, rise = _soliton()
    boxy = np.minimum(rise * 3.0, 30.0)
    assert measure_pulse(times, places, boxy).shape_misfit > 0.1


def test_hidden_columns_do_not_disturb_the_measurement():
    wave, times, places, rise = _soliton()
    rise[:, 60:70] = np.nan
    rise[40:44, :] = np.nan
    measured = measure_pulse(times, places, rise)
    assert measured.crest_rise_mm == pytest.approx(30.0, abs=0.1)
    assert measured.speed_mm_s == pytest.approx(wave.phase_speed_mm_s, rel=0.005)


def test_a_hidden_crest_is_reported_as_not_seen():
    _, times, places, rise = _soliton()
    top = int(np.argmax(station_series(places, rise, 650.0)))
    rise[top - 3:top + 4, :] = np.nan
    measured = measure_pulse(times, places, rise, station_mm=650.0)
    assert not measured.crest_seen
    assert measured.crest_rise_mm < 30.0


def test_a_wave_still_arriving_at_the_end_has_no_width_or_speed():
    _, times, places, rise = _soliton()
    cut = int(np.argmax(station_series(places, rise, 650.0))) + 2
    measured = measure_pulse(times[:cut], places, rise[:cut], station_mm=650.0)
    assert measured.half_height_seconds is None
    assert measured.fwhm_mm is None
    assert measured.shape_misfit is None


def test_speed_is_refused_over_too_short_a_stretch():
    _, times, places, rise = _soliton()
    arrivals = arrival_times(times, rise, 15.0)
    assert pulse_speed(places[:30], arrivals[:30]) is None
    assert pulse_speed(places[:5], arrivals[:5]) is None


def test_gaps_are_bridged_but_nothing_is_invented():
    assert fill_gaps(np.array([1.0, np.nan, 3.0])).tolist() == [1.0, 2.0, 3.0]
    assert np.isnan(fill_gaps(np.array([np.nan, np.nan]))).all()


def test_readings_of_the_wrong_shape_are_refused():
    with pytest.raises(ValueError):
        measure_pulse(np.arange(3.0), np.arange(4.0), np.zeros((4, 3)))
    with pytest.raises(ValueError, match="never seen"):
        measure_pulse(np.arange(3.0), np.arange(4.0), np.full((3, 4), np.nan))


def test_still_water_is_calm_and_a_passing_wave_is_not():
    random = np.random.default_rng(1)
    still = 150.0 + random.normal(0.0, 0.05, (60, 200))
    still[:, 17] += random.normal(0.0, 5.0, 60)
    assert restlessness_mm(still) < 0.1
    _, _, _, rise = _soliton()
    assert restlessness_mm(150.0 + rise[40:100]) > 1.0
    assert restlessness_mm(np.full((5, 4), np.nan)) == float("inf")


def test_a_record_is_plain_enough_to_save_as_json():
    import json
    _, times, places, rise = _soliton()
    record = measure_pulse(times, places, rise, still_depth_mm=150.0).to_record()
    assert json.loads(json.dumps(record))["crest_seen"] is True


# -- from pictures --------------------------------------------------------

def _from_pictures(**tank_options):
    pytest.importorskip("cv2")
    from vision.calibration import locate
    from vision.synthetic import SyntheticTank
    from vision.waterline import WaterlineReader

    tank = SyntheticTank(**tank_options)
    reader = WaterlineReader(tank.rig, locate(tank.frame(0.0), tank.rig))
    still = reader.learn_still([tank.frame(0.0) for _ in range(5)],
                               near_depth_mm=tank.depth_mm)
    times = np.arange(0.0, tank.crest_time_s(1400.0) + 1.5 * tank.fwhm_mm / tank.speed_mm_s,
                      1.0 / 20.0)
    rise = np.array([reader.rise(tank.frame(moment)) for moment in times])
    return tank, measure_pulse(times, reader.x_mm, rise,
                               still_depth_mm=float(np.nanmedian(still)))


@pytest.mark.parametrize("water_is_darker", [True, False])
def test_a_drawn_wave_is_measured_from_its_pictures(water_is_darker):
    tank, measured = _from_pictures(water_is_darker=water_is_darker)
    assert measured.crest_seen
    assert measured.crest_rise_mm == pytest.approx(tank.crest_mm, abs=0.5)
    assert measured.speed_mm_s == pytest.approx(tank.speed_mm_s, rel=0.01)
    assert measured.fwhm_mm == pytest.approx(tank.fwhm_mm, rel=0.02)
    assert measured.still_depth_mm == pytest.approx(tank.depth_mm, abs=0.5)
    assert measured.shape_misfit < 0.02


def test_a_wave_against_a_black_backdrop_is_measured():
    """Black cloth behind the tank: dark air, faintly lit water, a bright line
    where the water meets the glass, and the room reflected over all of it."""
    tank, measured = _from_pictures(black_backdrop=True)
    assert measured.crest_seen
    assert measured.crest_rise_mm == pytest.approx(tank.crest_mm, abs=0.5)
    assert measured.speed_mm_s == pytest.approx(tank.speed_mm_s, rel=0.01)
    assert measured.fwhm_mm == pytest.approx(tank.fwhm_mm, rel=0.02)
    # The reader sits on the edge of the bright line, not its middle, so the
    # depth carries an offset of about the blur.  Rise does not: the still
    # level it is measured from has the same offset.
    assert measured.still_depth_mm == pytest.approx(tank.depth_mm, abs=2.5)


def test_a_tall_wave_crossing_the_rail_is_still_measured():
    """The crest climbs through a rail on the glass that hides it for a moment."""
    tank, measured = _from_pictures(depth_mm=200.0, crest_mm=90.0, launch_x_mm=-2500.0)
    assert measured.crest_seen
    assert measured.crest_rise_mm == pytest.approx(90.0, abs=1.0)
    assert measured.fwhm_mm == pytest.approx(tank.fwhm_mm, rel=0.03)
