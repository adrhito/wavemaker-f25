"""A wavemaker that exists only in memory, so the application can be used and
demonstrated nowhere near the machine.

:class:`app.plc.SimulatedPlc` is a tag store: writes go in, reads come back.
That is right for tests, which want nothing to happen except what they ask for.
It is no good for trying the interface out, because nothing moves -- the live
tank view sits still and a travelling wave looks identical to a flat one.

:class:`SimulatedMachine` adds the missing half. It watches the command bits the
application writes and moves thirty pistons accordingly, publishing their
positions back to the tags the live view reads. Prepare really homes, Start
really strokes, and a curve offset staggered front to back really does travel
along the chamber.

What it models
--------------
* Homing: pistons run to the home position and report the homed bit.
* Single stroke and continuous motion between Position 1 and Position 2, at
  Speed 1 going out and Speed 2 coming back.
* A travelling wave, produced the way the machine produces one: the pistons
  are staggered along their stroke before the run starts and each carries on
  from where it is. The mock does not fake this with a start delay, because
  the controller has no such thing outside a curve run.
* Live Motors: pistons not in a set stay put.
* Following error: the commanded position and the actual position are published
  separately, and :meth:`SimulatedMachine.wear` makes a piston fall behind, so
  the straggler warning can be practised without waiting for a real one to
  seize.

What it does not model
----------------------
Fluid, wave height, drive current, faults, or anything else about the physics
of the tank. It moves rectangles so the interface can be exercised.
**Nothing here predicts what the real machine will do.**
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional

from app import params, tags
from app.plc import SimulatedPlc

#: Where a homed piston sits, in mm.
HOME_POSITION = 0.0

#: Seconds of simulated homing travel before the homed bit is reported.
HOME_SECONDS = 2.0

#: How often the machine advances. 50 Hz is far finer than the four-times-a-
#: second the display polls at, so motion looks smooth rather than stepped.
TICK = 0.02

#: How much of its acceleration each motion profile is allowed to use at the
#: very start and end of a leg. Trapezoidal slams straight to full
#: acceleration; the smoother profiles ease into it, which is the whole reason
#: an operator picks one. Values are a shaping factor, not a vendor curve --
#: the mock moves rectangles and does not pretend to reproduce the drive's
#: internal interpolation.
PROFILE_SMOOTHING = {
    0: 0.0,    # Trapezoidal: no easing
    1: 0.6,    # Bestehorn
    2: 0.8,    # S-Curve
    3: 1.0,    # Sine: fully eased
}




class _Piston:
    """One simulated piston, travelling between two positions."""

    def __init__(self, axis: int) -> None:
        self.axis = axis
        #: Where a perfect piston would be right now. Published as
        #: ComDemandPosition, which on the real drive is the instantaneous
        #: interpolated command, not the end of the stroke.
        self.demand = HOME_POSITION
        #: Where this piston actually is. Equal to the demand unless it is worn.
        self.position = HOME_POSITION
        #: 1.0 tracks the command exactly. Below that the piston drags; 0 is
        #: seized. Used to demonstrate the straggler warning.
        self.efficiency = 1.0
        self.homed = False
        self.live = False
        self.elapsed = 0.0
        #: True while travelling towards Position 2, False on the way back.
        self.outbound = True
        self.started = False
        #: How fast the command is moving, mm/s. Modelled rather than assumed,
        #: so Accel, Decel, Jerk and Profile do something visible.
        self.velocity = 0.0
        #: Current acceleration, mm/s^2. Kept between ticks because Jerk limits
        #: how fast it is allowed to change.
        self.accel = 0.0
        #: Seconds left of the dwell at the end of a leg, from Time 1/Time 2.
        self.dwell_left = 0.0

    def reset_cycle(self) -> None:
        self.elapsed = 0.0
        self.outbound = True
        self.started = False
        self.velocity = 0.0
        self.accel = 0.0
        self.dwell_left = 0.0


class SimulatedMachine(SimulatedPlc):
    """A tag store that also moves.

    Safe to use exactly like :class:`~app.plc.PlcClient`; the application cannot
    tell the difference apart from the banner saying it is not connected.
    """

    def __init__(self, tick: float = TICK) -> None:
        SimulatedPlc.__init__(self)
        self.tick = tick
        self.pistons: Dict[int, _Piston] = dict(
            (axis, _Piston(axis)) for axis in range(tags.MOTOR_COUNT)
        )
        self._homing_elapsed = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.start()

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="SimulatedMachine", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread = None
        SimulatedPlc.close(self)

    # -- the loop -------------------------------------------------------------

    def _run(self) -> None:
        last = time.time()
        while not self._stop.is_set():
            now = time.time()
            self._step(now - last)
            last = now
            self._stop.wait(self.tick)

    def _int(self, tag: str, default: int = 0) -> int:
        try:
            return int(self.read(tag))
        except (TypeError, ValueError):
            return default

    def _param(self, axis: int, name: str) -> int:
        spec = params.BY_NAME[name]
        return self._int(spec.tag(axis), spec.default)

    def _step(self, dt: float) -> None:
        if dt <= 0 or dt > 1.0:
            dt = self.tick

        homing = bool(self._int(tags.HOME_BUTTON))
        run_single = bool(self._int(tags.RUN_SINGLE))
        run_continuous = bool(self._int(tags.RUN_CONTINUOUS))
        run_curve = bool(self._int(tags.RUN_CURVE))
        running = run_single or run_continuous or run_curve

        if homing:
            self._home(dt)
        elif running:
            self._move(dt, one_stroke=run_single)
        else:
            for piston in self.pistons.values():
                piston.reset_cycle()

        self._publish()

    def _home(self, dt: float) -> None:
        self._homing_elapsed += dt
        for axis, piston in self.pistons.items():
            piston.live = bool(self._int(tags.live_motor(axis)))
            if not piston.live:
                continue
            # Travel home at a fixed, deliberately unhurried rate.
            step = 200.0 * dt
            if abs(piston.demand - HOME_POSITION) <= step:
                piston.demand = HOME_POSITION
            else:
                piston.demand += step if piston.demand < HOME_POSITION else -step
            self._follow(piston, 200.0, dt)
            if self._homing_elapsed >= HOME_SECONDS and piston.position == HOME_POSITION:
                piston.homed = True
            piston.reset_cycle()

    def _move(self, dt: float, one_stroke: bool) -> None:
        self._homing_elapsed = 0.0
        for axis, piston in self.pistons.items():
            piston.live = bool(self._int(tags.live_motor(axis)))
            if not piston.live:
                continue

            piston.elapsed += dt

            # Curve Offset used to be applied here as a start delay, which
            # made the mock show a travelling wave that the real machine could
            # not produce: the controller only reads Curve Offset during a
            # curve run, so in continuous motion every piston set off together.
            # The stagger is real now -- Model._stage_cascade puts the pistons
            # at different points along the stroke before the run -- and a
            # piston simply carries on from wherever it is, so the wave travels
            # here for the same reason it will travel at the machine.
            piston.started = True

            first = float(self._param(axis, "Position 1"))
            second = float(self._param(axis, "Position 2"))
            out_speed = max(float(self._param(axis, "Speed 1")), 1.0)
            back_speed = max(float(self._param(axis, "Speed 2")), 1.0)

            if one_stroke:
                # Run_1 is an absolute move to Position 1 and never reads
                # Position 2 -- measured on the real array, where a piston
                # already sitting at Position 1 did not move for three
                # successive pulses. The mock used to run a full out-and-back
                # here, which is why One stroke looked right in simulation and
                # did half of nothing at the machine. Model._single_stroke
                # builds a real stroke out of two of these moves.
                target = first
                speed = out_speed
            else:
                target = second if piston.outbound else first
                speed = out_speed if piston.outbound else back_speed

            # A dwell at the end of a leg, from Time 1 and Time 2. These are
            # milliseconds on the drive and were ignored here entirely, so a
            # preset built around a pause looked identical to one without.
            if piston.dwell_left > 0.0:
                piston.dwell_left = max(0.0, piston.dwell_left - dt)
                self._follow(piston, speed, dt)
                continue

            arrived = self._advance(piston, axis, target, speed, dt)
            if arrived:
                piston.demand = target
                piston.velocity = 0.0
                piston.accel = 0.0
                dwell_ms = float(self._param(
                    axis, "Time 2" if piston.outbound else "Time 1"))
                piston.dwell_left = max(dwell_ms, 0.0) / 1000.0
                if one_stroke:
                    self._follow(piston, speed, dt)
                    continue  # Run_1 arrives at Position 1 and stays there
                piston.outbound = not piston.outbound

            # The piston follows it, and a worn one cannot quite keep up.
            self._follow(piston, max(abs(piston.velocity), speed), dt)

    def _limit(self, axis: int, name: str) -> float:
        """A motion limit, falling back to the parameter's own default.

        The tag store starts every tag at zero, so a caller that never wrote an
        Accel would otherwise be simulated as a piston that can accelerate at
        zero and therefore never moves. An unwritten tag here means "nobody
        said", not "the drive was told nought".
        """
        value = float(self._param(axis, name))
        if value > 0:
            return value
        spec = params.BY_NAME.get(name)
        return float(spec.default) if spec and spec.default else 1.0

    def _advance(self, piston, axis: int, target: float, speed: float,
                 dt: float) -> bool:
        """Move the command towards ``target``, honouring the motion limits.

        Previously the command simply stepped at the requested speed and always
        arrived, which made Accel, Decel, Jerk and Profile do nothing at all --
        fourteen of the eighteen parameters were ignored here, so "Ripples" and
        "Storm" looked the same in the mock however different they are at the
        machine.

        This is a trapezoid with a jerk limit: accelerate up to Speed, run at
        it, then brake in time to stop on the target, with Jerk capping how
        fast the acceleration itself may change and Profile deciding how much
        the ends are eased. It moves rectangles, not water, and it is not the
        drive's own interpolator -- but the shape of the motion now follows the
        parameters instead of ignoring them.

        Returns True once the command has reached the target.
        """
        gap = target - piston.demand
        distance = abs(gap)
        if distance < 1e-6 and abs(piston.velocity) < 1e-6:
            return True

        direction = 1.0 if gap > 0 else -1.0
        accel_limit = self._limit(axis, "Accel 1" if piston.outbound else "Accel 2")
        decel_limit = self._limit(axis, "Decel 1" if piston.outbound else "Decel 2")
        jerk_limit = self._limit(axis, "Jerk 1" if piston.outbound else "Jerk 2")
        profile = int(self._param(axis, "Profile"))
        smoothing = PROFILE_SMOOTHING.get(profile, 0.6)

        speed_now = abs(piston.velocity)
        # How far it needs to brake from here. Stop accelerating before that.
        braking = (speed_now * speed_now) / (2.0 * decel_limit)
        wanted = -decel_limit if distance <= braking else (
            accel_limit if speed_now < speed else 0.0)

        # How long the acceleration takes to come up to its limit. Profile
        # decides most of it -- easing the ends is the reason to choose one --
        # and Jerk shortens or lengthens that easing.
        #
        # Jerk is NOT applied as mm/s^3 here. Taken literally, a Jerk of 7500
        # would need 2.7 s to reach an Accel of 20000, and the real array does
        # a 350 mm leg in under two seconds with those very numbers, so the
        # drive's units plainly are not those. They are not established
        # anywhere in this repository, so rather than invent them Jerk is used
        # as a relative control: higher jerk, crisper ramp.
        ease = smoothing * 0.15 * max(0.25, min(4.0, 5000.0 / jerk_limit))
        if ease <= 0.0:
            piston.accel = wanted
        else:
            max_change = (accel_limit / ease) * dt
            change = max(-max_change, min(max_change, wanted - piston.accel))
            piston.accel += change

        speed_now = max(0.0, min(speed_now + piston.accel * dt, speed))
        piston.velocity = speed_now * direction

        step = speed_now * dt
        if step >= distance:
            return True
        piston.demand += direction * step
        return False

    @staticmethod
    def _follow(piston, speed: float, dt: float) -> None:
        """Move the piston towards the commanded position.

        With ``efficiency`` at 1.0 it keeps up exactly, so demanded and actual
        agree and nothing is flagged. Below that it falls behind, which is what
        a worn or dragging piston does and what the straggler warning is for.
        """
        reach = speed * piston.efficiency * dt
        gap = piston.demand - piston.position
        if abs(gap) <= reach:
            piston.position = piston.demand
        else:
            piston.position += reach if gap > 0 else -reach

    def _publish(self) -> None:
        """Write the pistons' state into the tags the application reads.

        Positions go out in drive counts of 0.1 micrometres, which is what the
        real controller publishes -- see
        :data:`app.params.POSITION_COUNTS_PER_MM`. The mock previously published
        millimetres, which meant it could not have caught the unit mismatch that
        made every healthy piston look thousands of millimetres out of position
        on the real machine.
        """
        for axis, piston in self.pistons.items():
            self.values[tags.axis_field(axis, tags.ACTUAL_POSITION)] = int(
                round(params.to_counts(piston.position))
            )
            self.values[tags.axis_field(axis, tags.DEMAND_POSITION)] = int(
                round(params.to_counts(piston.demand))
            )
            # Bit 11 of the status word is the homed bit; see Motor.HOMED_BIT.
            self.values[tags.axis_field(axis, tags.STATUS_WORD)] = (
                (1 << 11) if piston.homed else 0
            )

    # -- writes that mean something -------------------------------------------

    def write(self, tag: str, value: int) -> None:
        SimulatedPlc.write(self, tag, value)

        # Clearing faults or turning the motors off forgets that the pistons
        # were homed, exactly as a real reset would.
        if tag == tags.CLEAR_MOTOR_ERROR and value:
            for piston in self.pistons.values():
                piston.homed = False
                piston.reset_cycle()
        if tag == tags.HOME_BUTTON and not value:
            self._homing_elapsed = 0.0

    def identity(self) -> Optional[str]:
        return "Simulated wavemaker (mock, no hardware)"

    # -- helpers for demonstrating --------------------------------------------

    def snapshot(self) -> Dict[int, float]:
        """Every piston's position right now."""
        return dict((a, p.position) for a, p in self.pistons.items())

    def place(self, axis: int, position: float) -> None:
        """Put a piston at a position without moving it there.

        The machine equivalent is ``Model._stage_cascade``, which walks the
        pistons to their staggered starting points before a continuous run.
        Here it is instantaneous, because staging is not the thing being
        simulated -- what happens *after* it is.
        """
        piston = self.pistons[axis]
        piston.demand = float(position)
        piston.position = float(position)
        self._publish()

    def wear(self, axis: int, efficiency: float) -> None:
        """Make a piston drag, for practising with the straggler warning.

        ``1.0`` is healthy, ``0.7`` visibly falls behind, ``0`` is seized --
        "one of the motors will go up real slow, and then that one has gotten
        itself stuck".
        """
        self.pistons[axis].efficiency = max(0.0, min(1.0, efficiency))

    def clear_wear(self) -> None:
        for piston in self.pistons.values():
            piston.efficiency = 1.0
