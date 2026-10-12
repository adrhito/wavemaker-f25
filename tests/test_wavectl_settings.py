"""Choosing pistons, setting parameters and applying presets through wavectl."""

from __future__ import annotations

import pytest

from app import params, tags
from Model import MachineState
from wavectl import pistons, settings
from wavectl.service import Service


@pytest.fixture
def service(model) -> Service:
    return Service(model, "mock")


def axis(shown: int) -> int:
    return tags.axis_from_display(shown)


class TestReadingWhatWasTyped:
    def test_speed_means_both_directions(self):
        edit = settings.read({"speed": "150"})
        assert edit.values == {"Speed 1": 150, "Speed 2": 150}

    def test_full_names_are_taken_without_their_spaces(self):
        assert settings.read({"Speed2": "90", "curve_offset": "17"}).values == {
            "Speed 2": 90, "Curve Offset": 17}

    def test_a_profile_may_be_named(self):
        assert settings.read({"profile": "scurve"}).values == {"Profile": 2}

    def test_a_stroke_is_measured_from_position_one(self):
        edit = settings.read({"p1": "50", "stroke": "120"})
        assert settings.changed(params.defaults(), edit) == {
            "Position 1": 50, "Position 2": 170}

    @pytest.mark.parametrize("pairs", [
        {"height": "3"}, {"speed": "fast"}, {"stroke": "0"}, {"rest": "sideways"}])
    def test_what_cannot_be_read_is_refused(self, pairs):
        with pytest.raises(ValueError):
            settings.read(pairs)

    def test_limits_come_from_the_parameter_table(self):
        with pytest.raises(ValueError) as refused:
            settings.changed(params.defaults(), settings.read({"speed": "901"}))
        assert "at most 900" in str(refused.value)


class TestSelecting:
    def test_select_replaces_the_selection(self, service, model):
        assert service.handle("select", {"pistons_text": "1-9"})["ok"]
        reply = service.handle("select", {"pistons_text": "c2"})
        assert reply["data"]["pistons"] == [4, 5, 6]
        assert model.live_axes == sorted(axis(n) for n in (4, 5, 6))

    def test_kept_pistons_keep_their_parameters(self, service, model):
        service.handle("select", {"pistons_text": "1-3"})
        service.handle("set", {"values": {"speed": "150"}})
        service.handle("select", {"pistons_text": "1-6"})
        assert set(m.write_params["Speed 1"] for m in model.all_motors) == {150}

    def test_not_while_the_machine_is_moving(self, service, model):
        service.handle("select", {"pistons_text": "1-3"})
        model._set_state(MachineState.RUNNING)
        reply = service.handle("select", {"pistons_text": "1-9"})
        assert not reply["ok"] and "stop it before changing" in reply["text"]
        assert len(model.live_axes) == 3

    def test_a_bad_list_changes_nothing(self, service, model):
        service.handle("select", {"pistons_text": "1-3"})
        assert not service.handle("select", {"pistons_text": "1-40"})["ok"]
        assert len(model.live_axes) == 3


class TestSetting:
    def test_stroke_and_speed_on_the_whole_selection(self, service, model):
        service.handle("select", {"pistons_text": "1-9"})
        reply = service.handle("set", {"values": {"stroke": "120", "speed": "150"}})
        assert reply["ok"]
        assert "pistons 1-9 | 0 to 120 mm at 150 mm/s" in reply["text"]
        for motor in model.all_motors:
            assert motor.write_params["Position 2"] == 120
            assert motor.write_params["Speed 2"] == 150

    def test_nothing_reaches_the_controller(self, service, plc):
        service.handle("select", {"pistons_text": "1-9"})
        service.handle("set", {"values": {"stroke": "120"}})
        assert plc.history == []

    def test_one_value_out_of_range_changes_no_piston(self, service, model):
        service.handle("select", {"pistons_text": "1-3"})
        service.handle("set", {"values": {"p1": "300"}, "pistons_text": "2"})
        # 300 + 100 is past the 370 limit for piston 2 only.
        reply = service.handle("set", {"values": {"stroke": "100"}})
        assert not reply["ok"]
        assert "Piston(s) 2: Position 2 must be at most 370" in reply["text"]
        assert [m.write_params["Position 2"] for m in model.all_motors] == [350] * 3

    def test_some_pistons_of_the_selection(self, service, model):
        service.handle("select", {"pistons_text": "1-6"})
        service.handle("set", {"values": {"speed": "200"}, "pistons_text": "1-3"})
        speeds = dict((tags.display_number(m.axis), m.write_params["Speed 1"])
                      for m in model.all_motors)
        assert speeds[1] == speeds[3] == 200 and speeds[4] != 200
        assert "speeds differ" in service.handle("status")["text"]

    def test_a_piston_outside_the_selection_is_refused(self, service):
        service.handle("select", {"pistons_text": "1-3"})
        reply = service.handle("set", {"values": {"speed": "200"},
                                       "pistons_text": "20"})
        assert not reply["ok"] and "not selected" in reply["text"]

    def test_with_nothing_selected_it_seeds_the_next_selection(self, service, model):
        assert service.handle("set", {"values": {"speed": "200"}})["ok"]
        service.handle("select", {"pistons_text": "1-3"})
        assert set(m.write_params["Speed 1"] for m in model.all_motors) == {200}

    def test_an_edit_after_homing_does_not_ask_for_homing_again(
            self, service, homed_model):
        assert homed_model.state is MachineState.HOMED
        reply = service.handle("set", {"values": {"speed": "200"}})
        assert "ready, still homed" in reply["text"]

    def test_rest_belongs_to_the_session(self, service, model):
        service.handle("select", {"pistons_text": "1-3"})
        assert service.handle("set", {"values": {"rest": "hold"}})["ok"]
        assert model.rest_position == "hold"

    def test_params_names_all_eighteen(self, service):
        service.handle("select", {"pistons_text": "1-3"})
        service.handle("set", {"values": {"p1": "50"}, "pistons_text": "1"})
        text = service.handle("params")["text"]
        assert text.startswith("pistons 1-3: Position 1 differs, Position 2 ")
        assert all(spec.name in text for spec in params.PARAMS)


@pytest.fixture
def preset_folder(tmp_path):
    """The model fixture points the preset folder at an empty temporary one."""
    folder = tmp_path / "Presets"
    folder.mkdir(parents=True, exist_ok=True)
    header = "Motor," + ",".join(params.PARAM_NAMES)

    def row(label, speed, position_two=350):
        values = params.defaults()
        values.update({"Speed 1": speed, "Speed 2": speed,
                       "Position 2": position_two})
        return "{0},{1}".format(label, ",".join(
            str(values[name]) for name in params.PARAM_NAMES))

    front = [row(a, 400) if a in (27, 28, 29) else row(a, 0) for a in range(30)]
    (folder / "Front column.csv").write_text(
        "\n".join([header] + front) + "\n", encoding="utf-8")
    (folder / "Front and far.csv").write_text(
        "\n".join([header] + [row(a, 300, 999) for a in range(30)]) + "\n",
        encoding="utf-8")
    return folder


class TestPresets:
    def test_the_presets_are_listed(self, service, preset_folder):
        assert service.handle("presets")["data"]["presets"] == [
            "Front and far", "Front column"]

    def test_the_start_of_a_name_is_enough(self, service, model, preset_folder):
        service.handle("select", {"pistons_text": "1-3"})
        reply = service.handle("preset", {"name": "front c"})
        assert reply["ok"] and reply["text"].startswith("applied Front column")
        assert set(m.write_params["Speed 1"] for m in model.all_motors) == {400}

    def test_an_ambiguous_start_names_the_candidates(self, service, preset_folder):
        service.handle("select", {"pistons_text": "1-3"})
        reply = service.handle("preset", {"name": "front"})
        assert not reply["ok"]
        assert "Front and far; Front column" in reply["text"]

    def test_select_takes_the_pistons_the_preset_moves(self, service, model,
                                                       preset_folder):
        reply = service.handle("preset", {"name": "Front column", "select": True})
        assert reply["ok"]
        assert pistons.shown(model.live_axes) == [1, 2, 3]

    def test_without_a_selection_it_says_which_pistons_it_moves(self, service,
                                                                 preset_folder):
        reply = service.handle("preset", {"name": "Front column"})
        assert not reply["ok"] and "pistons 1-3" in reply["text"]

    def test_a_value_past_the_limit_is_held_there_and_said(self, service, model,
                                                           preset_folder):
        service.handle("select", {"pistons_text": "1-3"})
        reply = service.handle("preset", {"name": "Front and far"})
        assert reply["ok"]
        assert "Position 2 999 held at 370" in reply["text"]
        assert set(m.write_params["Position 2"] for m in model.all_motors) == {370}
