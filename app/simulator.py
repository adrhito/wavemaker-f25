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
* Curve Offset as a start delay, so a staggered offset produces a visible
  travelling wave. This is the whole point of the mock.
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

#: Curve Offset is treated as this many milliseconds of start delay per unit.
#: Entirely a convention of this mock -- it exists so that staggering the offset
#: across columns visibly delays the back of the chamber. The real meaning of
#: Curve Offset on the PLC is not known and is not claimed here.
OFFSET_MS_PER_UNIT = 10.0


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

    def reset_cycle(self) -> None:
        self.elapsed = 0.0
        self.outbound = True
        self.started = False


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

            # Curve Offset delays the start, so a front-to-back stagger makes
            # the wave arrive at the back of the chamber later.
            delay = self._param(axis, "Curve Offset") * OFFSET_MS_PER_UNIT / 1000.0
            if piston.elapsed < delay:
                continue
            piston.started = True

            first = float(self._param(axis, "Position 1"))
            second = float(self._param(axis, "Position 2"))
            out_speed = max(float(self._param(axis, "Speed 1")), 1.0)
            back_speed = max(float(self._param(axis, "Speed 2")), 1.0)

            target = second if piston.outbound else first
            speed = out_speed if piston.outbound else back_speed

            # The command moves at the requested speed and always arrives.
            step = speed * dt
            if abs(target - piston.demand) <= step:
                piston.demand = target
                if one_stroke and not piston.outbound:
                    self._follow(piston, speed, dt)
                    continue  # a single stroke ends back at Position 1
                piston.outbound = not piston.outbound
            else:
                piston.demand += step if target > piston.demand else -step

            # The piston follows it, and a worn one cannot quite keep up.
            self._follow(piston, speed, dt)

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
