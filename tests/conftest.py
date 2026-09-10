"""Shared fixtures.

Every test runs against :class:`app.plc.SimulatedPlc`; nothing here can reach a
real machine.  The old ``testing/`` package built a ``Model`` and a ``Tk`` root
at module scope, so merely importing it opened a connection to 192.168.1.1 and
wrote thirty tags.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import Model as model_module  # noqa: E402
from app import tags  # noqa: E402
from app.plc import SimulatedPlc  # noqa: E402
from Model import Model  # noqa: E402


@pytest.fixture(autouse=True)
def instant_timings(monkeypatch):
    """Remove the machine's wait states so tests run in milliseconds."""
    for name in (
        "BOOT_PULSE_SECONDS",
        "CLEAR_FAULT_SECONDS",
        "SINGLE_STROKE_SECONDS",
        "CURVE_SECONDS",
        "HOME_POLL_SECONDS",
    ):
        monkeypatch.setattr(model_module, name, 0.0)


@pytest.fixture
def plc() -> SimulatedPlc:
    return SimulatedPlc()


@pytest.fixture
def model(plc, tmp_path, monkeypatch) -> Model:
    """A model wired to a simulator, running commands inline.

    ``_spawn`` is replaced so commands run on the calling thread: a test can
    then assert on the result immediately instead of polling a worker.
    """
    monkeypatch.setattr("app.paths.ANALYTICS_DIR", tmp_path / "analytics")
    monkeypatch.setattr("app.paths.LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("app.paths.PRESET_DIR", tmp_path / "Presets")

    built = Model(transport=plc, is_live=True)
    built._spawn = lambda name, work: work()
    return built


@pytest.fixture
def homed_model(model, plc) -> Model:
    """A model with one set of three pistons, prepared and homed."""
    for axis in (0, 1, 2):
        model.toggle(axis, True)
    model.create_set()
    for axis in range(tags.MOTOR_COUNT):
        plc.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)
    assert model.prepare()
    plc.clear_history()
    return model


@pytest.fixture
def make_group():
    """Create a group of pistons with given parameters.

    Groups are implicit until the operator asks for a second one, so the first
    group is just a selection and any group after that needs add_group() first.
    This hides that from tests that only care about having two groups.
    """

    def build(model, axes, **parameters):
        if model.sets and model._implicit_group:
            model.add_group()
        for axis in axes:
            model.toggle(axis, True)
        for name, value in parameters.items():
            model.set_pending_param(name.replace("_", " ").title(), value)
        if model._implicit_group:
            group = model.sets[-1]
            for name, value in parameters.items():
                group.set_param(name.replace("_", " ").title(), value)
            return group
        return model.create_set()

    return build
