"""Motor sets keeping their own parameters, and preset files loading correctly."""

from __future__ import annotations

import csv

import pytest

from app import params, tags
from Model import MachineState, RunMode
from preset_options.PresetProcessor import PresetError, PresetProcessor


class TestPerSetParameters:
    """The feature the application exists for."""

    def test_sets_keep_independent_parameters(self, model, make_group):
        """Editing one set must not touch another.

        The previous version applied every parameter edit to every motor in
        every set, so a second set could only ever be a copy of the first --
        which is exactly the capability the Spring 24 work was meant to add.
        """
        first = make_group(model, (0, 1), speed_1=700)
        second = make_group(model, (2, 3), speed_1=250)

        assert first.common_value("Speed 1") == 700
        assert second.common_value("Speed 1") == 250

        second.set_param("Speed 1", 400)
        assert first.common_value("Speed 1") == 700
        assert second.common_value("Speed 1") == 400

    def test_each_set_writes_its_own_values_to_its_own_motors(
        self, model, plc, make_group
    ):
        make_group(model, (0, 1), speed_1=700)
        make_group(model, (2, 3), speed_1=250)

        for axis in range(tags.MOTOR_COUNT):
            plc.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)
        assert model.prepare()

        assert plc.read("Program:Wave_Control.Motor_1.Spd_1") == 700
        assert plc.read("Program:Wave_Control.Motor_2.Spd_1") == 700
        assert plc.read("Program:Wave_Control.Motor_3.Spd_1") == 250
        assert plc.read("Program:Wave_Control.Motor_4.Spd_1") == 250

    def test_a_motor_cannot_be_in_two_sets(self, model):
        """Two sets sharing a piston would write different parameters to the
        same drive, with whichever wrote last silently winning."""
        model.toggle(0, True)
        model.add_group()          # groups become explicit from here
        model.toggle(0, True)
        with pytest.raises(ValueError, match="(?i)already in another set"):
            model.create_set()

    def test_common_value_reports_disagreement(self, model):
        for axis in (0, 1):
            model.toggle(axis, True)
        motor_set = model.create_set()
        motor_set.motors[0].set_param("Speed 1", 100)
        assert motor_set.common_value("Speed 1") is None

    def test_creating_a_set_requires_a_selection(self, model):
        with pytest.raises(ValueError, match="at least one motor"):
            model.create_set()

    def test_removing_a_set_frees_its_motors(self, model):
        model.toggle(0, True)
        motor_set = model.create_set()
        model.remove_set(motor_set)
        assert model.axis_owner(0) is None
        assert model.state is MachineState.IDLE

    def test_editing_after_homing_returns_to_ready(self, homed_model):
        assert homed_model.state is MachineState.HOMED
        homed_model.sets[0].set_param("Speed 1", 123)
        homed_model.mark_unprepared()
        assert homed_model.state is MachineState.READY

    def test_changed_parameters_are_pushed_on_start_without_rehoming(
        self, homed_model, plc
    ):
        homed_model.sets[0].set_param("Speed 1", 321)
        plc.clear_history()
        homed_model.start(RunMode.SINGLE)
        assert plc.read("Program:Wave_Control.Motor_1.Spd_1") == 321


class TestPresetLoading:
    def test_blank_separator_rows_are_skipped(self, tmp_path):
        """``massive.csv`` in the shipped Presets folder has blank rows.

        The old reader called ``int('')`` on them, and the caller swallowed the
        exception into a debug log -- so the preset silently never loaded.
        """
        path = tmp_path / "gappy.csv"
        header = ["Motor"] + params.PARAM_NAMES
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerow([""] * len(header))
            writer.writerow([0] + [0] * len(params.PARAM_NAMES))
            writer.writerow([""] * len(header))
            writer.writerow(["All", 0, 350, 500, 500, 10000, 10000, 10000, 10000,
                             2000, 2000, 0, 0, 1, 0, 0, 0, 0, 0])

        preset = PresetProcessor().load(str(path))
        assert preset.all_row is not None
        assert preset.all_row["Speed 1"] == 500

    def test_a_placeholder_row_falls_back_to_the_all_row(self, tmp_path):
        """A zero row means "this piston was not in the preset", not "hold at 0".

        Saved presets write zeroes for every piston that was not live. The old
        apply used those rows verbatim, so applying a preset to a set containing
        any such piston quietly gave it zero speed and it simply did not move.
        """
        path = tmp_path / "partial.csv"
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Motor"] + params.PARAM_NAMES)
            for axis in range(30):
                if axis == 5:
                    writer.writerow([axis, 0, 350, 900, 900, 20000, 20000, 20000,
                                     20000, 7500, 7500, 0, 0, 1, 0, 0, 0, 0, 0])
                else:
                    writer.writerow([axis] + [0] * len(params.PARAM_NAMES))
            writer.writerow(["All", 0, 300, 450, 450, 10000, 10000, 10000, 10000,
                             2000, 2000, 0, 0, 1, 0, 0, 0, 0, 0])

        preset = PresetProcessor().load(str(path))
        assert preset.values_for(5)["Speed 1"] == 900   # its own row
        assert preset.values_for(9)["Speed 1"] == 450   # falls back to All

    def test_every_shipped_preset_loads(self):
        """All fourteen files in Presets/ must load without an exception."""
        from app import paths

        processor = PresetProcessor()
        files = sorted(paths.PRESET_DIR.glob("*.csv"))
        assert files, "no presets found to check"
        for path in files:
            preset = processor.load(str(path))
            assert preset.preview()

    def test_a_bad_file_raises_a_useful_message(self, tmp_path):
        path = tmp_path / "wrong.csv"
        path.write_text("Nonsense,Columns\n1,2\n", encoding="utf-8")
        with pytest.raises(PresetError, match="Motor"):
            PresetProcessor().load(str(path))

    def test_missing_columns_are_named(self, tmp_path):
        path = tmp_path / "short.csv"
        path.write_text("Motor,Position 1\n0,10\n", encoding="utf-8")
        with pytest.raises(PresetError, match="missing"):
            PresetProcessor().load(str(path))

    def test_non_numeric_cells_warn_and_default(self, tmp_path):
        path = tmp_path / "typo.csv"
        row = dict((name, 0) for name in params.PARAM_NAMES)
        row["Speed 1"] = "fast"
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Motor"] + params.PARAM_NAMES)
            writer.writerow([0] + [row[n] for n in params.PARAM_NAMES])
        preset = PresetProcessor().load(str(path))
        assert preset.warnings
        assert preset.rows[0]["Speed 1"] == params.BY_NAME["Speed 1"].default


class TestPresetSaving:
    def test_saving_uses_every_confirmed_set(self, model, tmp_path, make_group):
        """The old writer read ``live_motors``, which was empty once sets had
        been confirmed, so saving produced a file of zeroes."""
        make_group(model, (0, 1), speed_1=700)
        make_group(model, (2,), speed_1=250)

        target = PresetProcessor(model).save(str(tmp_path / "out.csv"), model.sets)
        preset = PresetProcessor().load(target)

        assert preset.rows[0]["Speed 1"] == 700
        assert preset.rows[1]["Speed 1"] == 700
        assert preset.rows[2]["Speed 1"] == 250

    def test_round_trip_preserves_values(self, model, tmp_path, make_group):
        make_group(model, (4, 5), position_2=300, accel_1=15000)

        target = PresetProcessor(model).save(str(tmp_path / "rt.csv"), model.sets)
        preset = PresetProcessor().load(target)
        assert preset.values_for(4)["Position 2"] == 300
        assert preset.values_for(4)["Accel 1"] == 15000

    def test_saving_with_no_sets_is_refused(self, model, tmp_path):
        with pytest.raises(PresetError, match="no motor sets"):
            PresetProcessor(model).save(str(tmp_path / "x.csv"), [])

    def test_an_empty_name_is_refused(self, model):
        with pytest.raises(PresetError, match="name"):
            PresetProcessor(model)._resolve_path("   ")


class TestAddingASecondGroup:
    """Pressing Add group then selecting pistons must actually build the
    second group.

    It did not: the selection only ever tracked group one, so after Add group
    nothing happened at all and there was no way to create another group from
    the interface.
    """

    def test_selecting_after_add_group_builds_the_next_group(self, model):
        model.set_selection([0, 1, 2])
        assert len(model.sets) == 1

        model.add_group()
        assert model.selected_axes() == []

        model.set_selection([10, 11])
        assert [s.name for s in model.sets] == ["Group 1", "Group 2"]
        assert model.sets[0].axes == [0, 1, 2]
        assert model.sets[1].axes == [10, 11]

    def test_earlier_groups_are_frozen(self, model):
        model.set_selection([0, 1, 2])
        model.sets[0].set_param("Speed 1", 700)
        model.add_group()
        model.set_selection([10, 11])
        model.sets[1].set_param("Speed 1", 260)

        assert model.sets[0].common_value("Speed 1") == 700
        assert model.sets[1].common_value("Speed 1") == 260

        # Changing the selection now only reshapes the newest group.
        model.set_selection([10, 11, 12])
        assert model.sets[0].axes == [0, 1, 2]
        assert model.sets[1].axes == [10, 11, 12]

    def test_pistons_in_a_frozen_group_cannot_be_taken(self, model):
        model.set_selection([0, 1, 2])
        model.add_group()
        model.set_selection([2, 10])          # 2 already belongs to Group 1
        assert model.sets[0].axes == [0, 1, 2]
        assert model.sets[1].axes == [10]

    def test_add_group_needs_something_to_freeze(self, model):
        with pytest.raises(ValueError, match="(?i)select some pistons"):
            model.add_group()

    def test_three_groups(self, model):
        for start in (0, 10, 20):
            model.set_selection([start, start + 1])
            if start != 20:
                model.add_group()
        assert [s.name for s in model.sets] == ["Group 1", "Group 2", "Group 3"]
        assert [s.axes for s in model.sets] == [[0, 1], [10, 11], [20, 21]]
