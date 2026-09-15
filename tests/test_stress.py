"""Deterministic offline fault injection at every movement-test write.

Only SimulatedPlc is used. These checks concern command/selection ordering;
they do not claim that simulated positions validate physical motion.
"""

import pytest

import Model as model_module
from app import tags
from app.plc import PlcError, SimulatedPlc


# Successful probe: isolate 30 axes, write five parameters, issue two moves
# with their targets/run edges, then restore 30 selection bits (71 writes).
PROBE_WRITES = range(1, 72)
TARGET_AXIS = 17


def _prepare_probe(model, plc, monkeypatch):
    assert isinstance(plc, SimulatedPlc)
    monkeypatch.setattr(model_module, "PROBE_SECONDS", 0)
    for axis in (0, 7, 29):
        plc.write(tags.live_motor(axis), 1)
    original = [plc.read(tags.live_motor(a)) for a in range(tags.MOTOR_COUNT)]
    plc.clear_history()
    return original


@pytest.mark.parametrize("failure_at", PROBE_WRITES)
def test_probe_single_write_failure_preserves_motion_isolation(
    model, plc, monkeypatch, failure_at,
):
    original = _prepare_probe(model, plc, monkeypatch)
    write = plc.write
    attempts = []
    violations = []
    failed = []

    def inject(tag, value):
        attempts.append((tag, value))
        if len(attempts) == failure_at:
            failed.append((tag, value))
            raise PlcError("Injected transient write failure {0}".format(failure_at))
        write(tag, value)
        if plc.read(tags.RUN_SINGLE):
            active = [a for a in range(tags.MOTOR_COUNT) if plc.read(tags.live_motor(a))]
            if active != [TARGET_AXIS]:
                violations.append((tag, value, active))

    monkeypatch.setattr(plc, "write", inject)
    with pytest.raises(PlcError):
        model.probe_movement(TARGET_AXIS)

    assert failed, "The injection point must be exercised"
    assert not model.busy, "Failure must release command ownership"
    assert not violations, "Only the probe target may be selected during Run_1"
    if plc.read(tags.RUN_SINGLE):
        # A rejected final stop cannot be treated as a confirmed stop. The
        # machine must remain isolated and the operator must see the failure.
        assert failed == [(tags.RUN_SINGLE, 0)]
        assert model.bridge.problems
        assert [a for a in range(tags.MOTOR_COUNT) if plc.read(tags.live_motor(a))] == [TARGET_AXIS]
        assert not any(tag.startswith(tags.PROGRAM + ".Live_Motors.")
                       for tag, value in attempts[failure_at:])
    else:
        assert plc.read(tags.RUN_CONTINUOUS) == 0
        assert plc.read(tags.RUN_CURVE) == 0
        restored = [plc.read(tags.live_motor(a)) for a in range(tags.MOTOR_COUNT)]
        if restored != original:
            assert model.bridge.problems, "Incomplete selection restoration must be visible"


@pytest.mark.parametrize("cancel_at", PROBE_WRITES)
def test_stop_at_each_probe_write_never_restarts_motion(model, plc, monkeypatch, cancel_at):
    _prepare_probe(model, plc, monkeypatch)
    write = plc.write
    attempts = []
    after_stop = []
    stopped = []
    violations = []

    def interrupt(tag, value):
        attempts.append((tag, value))
        if stopped:
            after_stop.append((tag, value))
        write(tag, value)
        if plc.read(tags.RUN_SINGLE):
            active = [a for a in range(tags.MOTOR_COUNT) if plc.read(tags.live_motor(a))]
            if active != [TARGET_AXIS]:
                violations.append(active)
        if len(attempts) == cancel_at and not stopped:
            stopped.append(True)
            model.stop(immediate=True, park=False)

    monkeypatch.setattr(plc, "write", interrupt)
    model.probe_movement(TARGET_AXIS)
    assert stopped, "The interruption point must be exercised"
    assert not violations
    assert not model.busy
    assert not any(tag in (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE,
                          tags.HOME_BUTTON) and value == 1 for tag, value in after_stop)
    assert all(plc.read(tag) == 0 for tag in
               (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE, tags.HOME_BUTTON))
