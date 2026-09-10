"""Parameter handling, validation, and what actually gets written to a drive."""

from __future__ import annotations

import pytest

from app import params, tags
from Motor import HOMED_BIT, Motor


class TestTagNaming:
    def test_motor_structures_are_one_based(self):
        """Axis 0 is Motor_1. Getting this wrong drives the neighbouring piston."""
        assert tags.motor_field(0, "Pos_1") == "Program:Wave_Control.Motor_1.Pos_1"
        assert tags.curve_field(0, "Curve_ID") == "Program:Wave_Control.Curve_1.Curve_ID"
        assert tags.motor_field(29, "Pos_1") == "Program:Wave_Control.Motor_30.Pos_1"

    def test_axis_structures_are_zero_based(self):
        assert tags.axis_field(0, "StatusWord") == "Program:Wave_Control.Axis[0].StatusWord"
        assert tags.live_motor(29) == "Program:Wave_Control.Live_Motors.29"

    @pytest.mark.parametrize("bad", [-1, 30, 100])
    def test_out_of_range_axis_is_refused(self, bad):
        with pytest.raises(ValueError):
            tags.live_motor(bad)

    def test_time_field_has_no_underscore(self):
        """Time1/Time2 differ from every other numbered field. Preserved from
        the original code, where it was easy to miss."""
        assert params.BY_NAME["Time 1"].field == "Time1"
        assert params.BY_NAME["Position 1"].field == "Pos_1"


class TestValidation:
    @pytest.mark.parametrize(
        "name,value",
        [
            ("Position 1", 371),
            ("Position 1", -21),
            ("Speed 1", 901),
            ("Speed 2", -1),
            ("Accel 1", 20001),
            ("Decel 2", -1),
            ("Profile", 4),
            ("Move Type", 2),
            ("Jerk 1", -1),
        ],
    )
    def test_rejects_out_of_range(self, name, value):
        assert params.BY_NAME[name].validate(value) is not None

    @pytest.mark.parametrize(
        "name,value",
        [("Position 1", 370), ("Position 1", -20), ("Speed 1", 900), ("Profile", 3)],
    )
    def test_accepts_the_boundaries(self, name, value):
        assert params.BY_NAME[name].validate(value) is None

    def test_defaults_are_valid(self):
        assert params.validate_all(params.defaults()) == []

    def test_missing_parameters_are_reported(self):
        incomplete = params.defaults()
        del incomplete["Speed 1"]
        assert any("Speed 1" in p for p in params.validate_all(incomplete))

    def test_parse_gives_an_operator_readable_message(self):
        with pytest.raises(ValueError, match="whole number"):
            params.parse("Speed 1", "fast")
        with pytest.raises(ValueError, match="at most"):
            params.parse("Speed 1", "1000")
        assert params.parse("Speed 1", "  450 ") == 450


class TestMotor:
    def test_layout_matches_the_physical_array(self):
        """Three rows of ten; row = axis % 3 + 1, column = axis // 3 + 1."""
        for axis in range(30):
            motor = Motor(axis)
            assert motor.row == axis % 3 + 1
            assert motor.column == axis // 3 + 1

    def test_only_changed_parameters_are_rewritten(self, plc):
        """A full write is 18 round trips per piston; resending unchanged
        values made preparing thirty pistons needlessly slow."""
        motor = Motor(0)
        motor.write_to(plc)
        assert len(plc.history) == len(params.PARAMS)

        plc.clear_history()
        motor.write_to(plc)
        assert plc.history == []

        motor.set_param("Speed 1", 600)
        plc.clear_history()
        motor.write_to(plc)
        assert plc.history == [("Program:Wave_Control.Motor_1.Spd_1", 600)]

    def test_write_order_puts_move_type_and_profile_first(self, plc):
        Motor(0).write_to(plc)
        written = [tag for tag, _ in plc.history]
        assert written[0].endswith(".MoveType")
        assert written[1].endswith(".Profile")

    def test_a_failed_write_does_not_claim_success(self, plc, monkeypatch):
        """``current_params`` must describe the machine, not the request.

        The old ``write_to_motor`` copied the whole requested dictionary into
        ``current_params`` regardless of what happened, so a partly failed write
        still compared equal and reported success.
        """
        from app.plc import PlcError

        motor = Motor(0)
        calls = {"n": 0}
        real_write = plc.write

        def flaky(tag, value):
            calls["n"] += 1
            if calls["n"] > 3:
                raise PlcError("connection lost")
            real_write(tag, value)

        monkeypatch.setattr(plc, "write", flaky)
        with pytest.raises(PlcError):
            motor.write_to(plc)

        assert len(motor.current_params) == 3
        assert not motor.is_synced

    def test_out_of_range_value_is_refused_before_any_write(self, plc):
        motor = Motor(0)
        motor.write_params["Speed 1"] = 5000
        with pytest.raises(ValueError, match="Speed 1"):
            motor.write_to(plc)
        assert plc.history == []

    def test_homed_reads_bit_eleven_of_the_status_word(self, plc):
        """The old code sliced ``bin(status)`` twelve characters from the right
        and raised whenever leading zeroes made the string too short -- that is,
        precisely when the drive was not ready."""
        motor = Motor(0)
        status_tag = tags.axis_field(0, tags.STATUS_WORD)

        plc.write(status_tag, 1 << HOMED_BIT)
        assert motor.is_homed(plc) is True

        plc.write(status_tag, 0)
        assert motor.is_homed(plc) is False

        # A small status word used to raise; it now simply means "not homed".
        plc.write(status_tag, 0b1)
        assert motor.is_homed(plc) is False

        plc.write(status_tag, (1 << HOMED_BIT) | 0b101)
        assert motor.is_homed(plc) is True

    def test_update_params_is_all_or_nothing(self):
        motor = Motor(0)
        original = motor.write_params["Speed 1"]
        with pytest.raises(ValueError):
            motor.update_params({"Speed 1": 800, "Position 1": 9999})
        assert motor.write_params["Speed 1"] == original
