"""Why a piston is stuck: the reasoning, not the reading."""

from __future__ import annotations

import pytest

from app import diagnostics, params, tags
from Motor import Motor


class FakePlc:
    """Answers with whatever the test put in, and refuses what it did not."""

    def __init__(self, values=None, silent=False):
        self.values = dict(values or {})
        self.silent = silent

    def read(self, tag):
        if self.silent:
            raise RuntimeError("no reply")
        if tag not in self.values:
            raise KeyError(tag)
        return self.values[tag]

    def write(self, tag, value):
        self.values[tag] = value


def drive(axis=14, status=0, warn=0, actual=0.0, demand=0.0,
          live=1, pos1=0, pos2=350, speed=500):
    """A drive answering with the words and positions given."""
    values = {
        tags.axis_field(axis, tags.STATUS_WORD): status,
        tags.axis_field(axis, tags.WARN_WORD): warn,
        tags.axis_field(axis, tags.STATE_VAR): 0,
        tags.axis_field(axis, tags.CONTROL_WORD): 0,
        tags.axis_field(axis, tags.ACTUAL_POSITION): params.to_counts(actual),
        tags.axis_field(axis, tags.DEMAND_POSITION): params.to_counts(demand),
        tags.live_motor(axis): live,
        params.BY_NAME["Position 1"].tag(axis): pos1,
        params.BY_NAME["Position 2"].tag(axis): pos2,
        params.BY_NAME["Speed 1"].tag(axis): speed,
        params.BY_NAME["Speed 2"].tag(axis): speed,
    }
    return FakePlc(values)


HEALTHY = (1 << 0) | (1 << 11)      # enabled and homed
ENABLED_NOT_HOMED = 1 << 0


def causes(report):
    return [finding.cause for finding in report.findings]


def headlines(report):
    return " | ".join(finding.headline for finding in report.findings)


class TestItSaysWhy:
    def test_a_healthy_piston_is_left_alone(self):
        report = diagnostics.diagnose(Motor(14), drive(status=HEALTHY))
        assert report.healthy
        assert "nothing wrong" in report.summary

    def test_a_piston_that_was_never_selected_is_not_blamed(self):
        """The failure that looks mechanical and is not.

        A piston whose Live_Motors bit is clear was skipped by the ladder. It
        never received a command, so of course it did not move -- and every
        reading on it is otherwise perfect.
        """
        report = diagnostics.diagnose(
            Motor(14), drive(status=ENABLED_NOT_HOMED, live=0), expected_live=True
        )
        assert "Software" in causes(report)
        assert "never included in the run" in headlines(report)

    def test_a_hot_motor_is_not_reported_as_a_jam(self):
        """Ordering matters: the lag is a symptom of the heat."""
        warn = (1 << 0) | (1 << 4)          # Motor Hot Sensor + Position Lag
        report = diagnostics.diagnose(
            Motor(14), drive(status=ENABLED_NOT_HOMED, warn=warn,
                             actual=0.0, demand=100.0)
        )
        assert report.findings[0].cause == "Thermal"
        assert "too hot" in report.findings[0].headline

    def test_a_drive_that_will_not_answer_says_so_and_stops(self):
        report = diagnostics.diagnose(Motor(14), FakePlc(silent=True))
        assert len(report.findings) == 1
        assert report.findings[0].cause == "Software"
        assert "not answering" in report.findings[0].headline

    def test_a_fatal_error_is_distinguished_from_a_clearable_one(self):
        fatal = diagnostics.diagnose(Motor(14), drive(status=(1 << 12)))
        clearable = diagnostics.diagnose(Motor(14), drive(status=(1 << 3)))
        assert "cannot be acknowledged" in fatal.findings[0].detail
        assert "Clear Faults" in clearable.findings[0].action

    def test_a_drive_holding_no_stroke_is_a_software_fault(self):
        """Position 1 == Position 2: it will sit still and report success."""
        report = diagnostics.diagnose(
            Motor(14), drive(status=HEALTHY, pos1=200, pos2=200)
        )
        assert "Software" in causes(report)
        assert "no stroke" in headlines(report)

    def test_parameters_that_did_not_reach_the_drive_are_caught(self):
        motor = Motor(14)
        motor.set_param("Position 2", 300)
        report = diagnostics.diagnose(
            motor, drive(status=HEALTHY, pos1=0, pos2=350)
        )
        assert "not holding the parameters" in headlines(report)

    def test_supply_voltage_is_not_called_mechanical(self):
        report = diagnostics.diagnose(
            Motor(14), drive(status=ENABLED_NOT_HOMED, warn=(1 << 2))
        )
        assert report.findings[0].cause == "Electrical"

    def test_a_disabled_drive_points_at_the_boot_sequence(self):
        report = diagnostics.diagnose(Motor(14), drive(status=0))
        assert "Software" in causes(report)
        assert "power stage is not enabled" in headlines(report)

    def test_not_homed_with_nothing_else_admits_it_does_not_know(self):
        report = diagnostics.diagnose(Motor(14), drive(status=ENABLED_NOT_HOMED))
        assert report.findings[0].cause == "Unknown"
        assert "nothing says why" in report.findings[0].headline


class TestTheMovementTest:
    """The question reading cannot answer."""

    def test_energised_and_did_not_move_is_certainly_mechanical(self):
        report = diagnostics.diagnose(
            Motor(14), drive(status=HEALTHY), probe=lambda: 0.0
        )
        mechanical = [f for f in report.findings if f.cause == "Mechanical"]
        assert mechanical, headlines(report)
        assert mechanical[0].confidence == "certain"
        assert "seized" in mechanical[0].action

    def test_a_piston_that_moves_is_not_accused_of_being_stuck(self):
        report = diagnostics.diagnose(
            Motor(14), drive(status=HEALTHY), probe=lambda: 10.0
        )
        assert "Mechanical" not in causes(report)

    def test_a_failed_probe_does_not_crash_the_diagnosis(self):
        def explode():
            raise RuntimeError("PLC went away")

        report = diagnostics.diagnose(
            Motor(14), drive(status=HEALTHY), probe=explode
        )
        assert isinstance(report.findings, list)


class TestTheReadings:
    def test_every_sensor_appears_in_the_checks(self):
        report = diagnostics.diagnose(
            Motor(14), drive(status=HEALTHY, actual=100.0, demand=104.0)
        )
        names = [check.name for check in report.checks]
        for wanted in ("Drive status word", "Power stage", "Drive warn word",
                       "Actual position", "Following error",
                       "Selected on the PLC (Live_Motors)",
                       "Stroke held on the drive"):
            assert wanted in names, names

    def test_following_error_is_flagged_once_it_is_large(self):
        report = diagnostics.diagnose(
            Motor(14), drive(status=HEALTHY, actual=0.0, demand=50.0)
        )
        error = next(c for c in report.checks if c.name == "Following error")
        assert error.verdict == "bad"
        assert "50.0 mm" in error.reading

    def test_routine_not_homed_warning_is_not_called_a_fault(self):
        """Warn bit 7 is set by every drive before its first homing."""
        report = diagnostics.diagnose(
            Motor(14), drive(status=HEALTHY, warn=(1 << 7))
        )
        warn = next(c for c in report.checks if c.name == "Drive warn word")
        assert warn.verdict == "ok"
        assert report.healthy
