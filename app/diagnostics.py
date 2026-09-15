"""Work out *why* a piston is stuck, not merely that it is.

"Piston 15 did not home" is where the old reporting stopped, and it left the
operator with nowhere to go: the same sentence covers a jammed shaft, a drive
that was never enabled, an overheating motor and a piston that was never
selected in the first place. Those have nothing in common and want four
different responses.

Everything the machine can say about one piston is read here -- the status
word, the warn word, the state variable, the control word, the commanded and
actual positions, the Live_Motors selection bit and the parameters actually
held on the drive -- and the combination is reasoned about rather than
reported raw.

The rules are ordered most specific first, so a piston that is both hot and
lagging is diagnosed as hot: the lag is a consequence, and calling a motor
that has tripped its thermal sensor "mechanically stuck" sends somebody to the
wrong end of the machine.

Nothing here moves anything. :func:`diagnose` only reads, unless it is handed
a probe to run.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, NamedTuple, Optional

from app import drive_status, params, tags

#: Following error, in mm, past which a piston is not merely trailing.
STUCK_ERROR_MM = 5.0
#: Movement, in mm, below which a probe counts as "did not move at all".
NO_MOVEMENT_MM = 1.0
# Configured drive travel (see app.params), not the narrower run input range.
DRIVE_MIN_MM = -57.0
DRIVE_MAX_MM = 453.0


def _valid_position(value):
    return (value is not None and math.isfinite(value)
            and DRIVE_MIN_MM <= value <= DRIVE_MAX_MM)


#: Causes that account for a piston not moving on their own. A drive that is
#: too hot, unpowered or faulted does not also need to be accused of a seized
#: shaft, and saying both sends somebody to the wrong end of the machine.
EXPLAINS_NO_MOVEMENT = ("Thermal", "Electrical", "Drive")


def _already_explained(found) -> bool:
    """True when a physical cause already accounts for a piston not moving.

    Deliberately narrower than "has anything been found at all". A Software
    finding -- "the drive is not holding the requested parameters", which fires
    whenever desired and held differ, so before every Prepare -- explains
    nothing mechanical. Letting it suppress the result meant a movement test
    the operator opted into, which physically moved the machine, could report
    zero travel and have that silently dropped.
    """
    return any(finding.cause in EXPLAINS_NO_MOVEMENT for finding in found)


def _parameters_relevant(facts):
    status = facts.get("status_word") or 0
    return not (drive_status.has_error(status) or _bit(status, 6))


def _stroke_expected(facts):
    # Selection alone does not mean a stroke has been commanded.
    return _bit(facts.get("status_word") or 0, 13)


def _stroke_wanted(facts):
    """The stroke the operator has asked for, or None if it is not known.

    Read from what the application holds for this piston rather than from the
    drive. A piston standing still *because* Position 1 and Position 2 are the
    same generates no setpoints, so status bit 13 is clear and
    :func:`_stroke_expected` is no help at all -- which is exactly the case
    worth flagging when a non-zero stroke was asked for.
    """
    wanted = facts.get("wanted_params") or {}
    try:
        return abs(float(wanted["Position 2"]) - float(wanted["Position 1"]))
    except (KeyError, TypeError, ValueError):
        return None


class Check(NamedTuple):
    """One thing that was looked at, and what it said."""

    name: str
    reading: str
    #: "ok", "warn", "bad" or "unknown" -- drives the colour in the UI.
    verdict: str = "ok"


class Finding(NamedTuple):
    """A conclusion, with somewhere to go next."""

    #: Mechanical, Thermal, Electrical, Drive, Software or Unknown.
    cause: str
    headline: str
    detail: str
    action: str
    #: "certain" when the machine states it outright, "likely" when inferred.
    confidence: str = "likely"


class Report(NamedTuple):
    axis: int
    checks: List[Check]
    findings: List[Finding]
    summary: str

    @property
    def healthy(self) -> bool:
        return not self.findings


def _bit(word: int, bit: int) -> bool:
    return bool((word >> bit) & 1)


def _read(plc, tag, default=None):
    """Read one tag, or return *default* if the machine will not say."""
    try:
        return plc.read(tag)
    except Exception:  # noqa: BLE001 - any failure here means "no reading"
        return default


def gather(motor, plc, expected_live: Optional[bool] = None) -> Dict[str, Any]:
    """Every reading this piston can give, in one pass.

    Returned as a plain dict so the rules below, and the tests, can work on a
    fixture with no machine attached.
    """
    axis = motor.axis
    facts = {"axis": axis, "expected_live": expected_live}  # type: Dict[str, Any]

    status = _read(plc, tags.axis_field(axis, tags.STATUS_WORD))
    warn = _read(plc, tags.axis_field(axis, tags.WARN_WORD))
    facts["status_word"] = None if status is None else int(status)
    facts["warn_word"] = None if warn is None else int(warn)
    facts["state_var"] = _read(plc, tags.axis_field(axis, tags.STATE_VAR))
    facts["control_word"] = _read(plc, tags.axis_field(axis, tags.CONTROL_WORD))

    actual = _read(plc, tags.axis_field(axis, tags.ACTUAL_POSITION))
    demand = _read(plc, tags.axis_field(axis, tags.DEMAND_POSITION))
    facts["actual_mm"] = None if actual is None else params.to_mm(actual)
    facts["demand_mm"] = None if demand is None else params.to_mm(demand)

    live = _read(plc, tags.live_motor(axis))
    facts["live"] = None if live is None else bool(int(live))

    held = {}  # type: Dict[str, Any]
    for name in ("Position 1", "Position 2", "Speed 1", "Speed 2"):
        spec = params.BY_NAME.get(name)
        if spec is not None:
            held[name] = _read(plc, spec.tag(axis))
    facts["held_params"] = held
    facts["wanted_params"] = dict(getattr(motor, "write_params", None) or {})
    return facts


def _stroke_held(facts: Dict[str, Any]) -> Optional[float]:
    held = facts.get("held_params") or {}
    try:
        return abs(float(held["Position 2"]) - float(held["Position 1"]))
    except (KeyError, TypeError, ValueError):
        return None


def checks_for(facts: Dict[str, Any]) -> List[Check]:
    """The readings, in the order an engineer would want to see them."""
    out = []  # type: List[Check]
    status = facts.get("status_word")
    warn = facts.get("warn_word")

    if status is None:
        out.append(Check("Drive status word", "no reply", "unknown"))
    else:
        out.append(Check(
            "Drive status word", "0x{0:04x}".format(status),
            "bad" if drive_status.has_error(status) else "ok",
        ))
        out.append(Check(
            "Power stage", "enabled" if _bit(status, 0) else "NOT enabled",
            "ok" if _bit(status, 0) else "bad",
        ))
        # Quick stop reads backwards: status bit 5 SET means quick stop is
        # *not* active, so it is the clear bit that stops the piston. Only
        # worth reading once the drive says it is enabled -- on an unpowered
        # drive every bit is clear and the power stage check above says so.
        if _bit(status, 0):
            out.append(Check(
                "Quick stop", "not active" if _bit(status, 5) else "ACTIVE",
                "ok" if _bit(status, 5) else "bad",
            ))
        out.append(Check(
            "Homed (status bit 11)", "yes" if _bit(status, 11) else "no",
            "ok" if _bit(status, 11) else "warn",
        ))
        out.append(Check(
            "Homing routine running", "yes" if _bit(status, 9) else "no"))
        out.append(Check("Motion active", "yes" if _bit(status, 13) else "no"))
        out.append(Check("At target position", "yes" if _bit(status, 10) else "no"))

    if warn is None:
        out.append(Check("Drive warn word", "no reply", "unknown"))
    else:
        named = drive_status.describe(warn, drive_status.WARN_BITS)
        out.append(Check(
            "Drive warn word",
            "0x{0:04x} ({1})".format(warn, ", ".join(named) if named else "clear"),
            "warn" if drive_status.problems(warn) else "ok",
        ))

    state = facts.get("state_var")
    if state is not None:
        out.append(Check("Drive state variable", "0x{0:04x}".format(int(state))))

    actual = facts.get("actual_mm")
    demand = facts.get("demand_mm")
    if actual is None:
        out.append(Check("Position sensor", "no reply", "unknown"))
    else:
        out.append(Check("Actual position", "{0:.1f} mm".format(actual),
                         "ok" if _valid_position(actual) else "bad"))
    if demand is not None:
        out.append(Check("Commanded position", "{0:.1f} mm".format(demand),
                         "ok" if _valid_position(demand) else "bad"))
    if actual is not None and demand is not None:
        error = abs(demand - actual)
        out.append(Check(
            "Following error",
            "{0:.1f} mm".format(error) if (_valid_position(actual) and
                _valid_position(demand)) else "unreliable position readings",
            ("bad" if error > STUCK_ERROR_MM else "ok") if
            (_valid_position(actual) and _valid_position(demand)) else "unknown",
        ))

    live = facts.get("live")
    if live is not None:
        out.append(Check(
            "Selected on the PLC (Live_Motors)", "yes" if live else "no",
            "bad" if facts.get("expected_live") and not live else "ok",
        ))

    stroke = _stroke_held(facts)
    if stroke is not None:
        if not _parameters_relevant(facts):
            verdict = "unknown"
        elif stroke >= 1:
            verdict = "ok"
        elif _stroke_expected(facts):
            # Moving, and yet holding no stroke: the drive is running a cycle
            # that goes nowhere.
            verdict = "bad"
        else:
            # Standing still. That is *why* bit 13 is clear, so it proves
            # nothing either way; what settles it is whether a stroke was
            # asked for. Amber rather than red: the drive may simply not have
            # been sent its parameters yet.
            wanted = _stroke_wanted(facts)
            verdict = "warn" if wanted is not None and wanted >= 1 else "ok"
        out.append(Check(
            "Stroke held on the drive", "{0:.0f} mm".format(stroke), verdict))

    moved = facts.get("probe_movement_mm")
    if facts.get("probe_error"):
        out.append(Check("Movement test", facts["probe_error"], "unknown"))
    if moved is not None:
        out.append(Check(
            "Movement when commanded", "{0:.1f} mm".format(moved),
            "bad" if moved < NO_MOVEMENT_MM else "ok",
        ))
    return out


def findings_for(facts: Dict[str, Any]) -> List[Finding]:
    """Reason over every reading at once. Most specific cause first."""
    found = []  # type: List[Finding]
    status = facts.get("status_word")
    warn = facts.get("warn_word")
    axis = facts.get("axis")
    name = tags.display_number(axis) if axis is not None else "?"

    if status is None and warn is None:
        found.append(Finding(
            "Software", "The drive is not answering",
            "Neither the status word nor the warn word could be read for "
            "piston {0}, so every other reading is missing for the same "
            "reason.".format(name),
            "Check the controller is in Run. If the other pistons answer and "
            "this one does not, the tags for this axis may be missing from the "
            "ladder.",
            "certain",
        ))
        return found

    warn = warn or 0
    status = status or 0

    # -- thermal first: it explains lag, and must not be read as a jam ------
    for bit, what in ((0, "The motor's own thermal sensor"),
                      (8, "PTC temperature sensor 1"),
                      (9, "PTC temperature sensor 2")):
        if _bit(warn, bit):
            found.append(Finding(
                "Thermal", "The motor is too hot",
                "{0} has tripped on piston {1}. A hot motor is current-limited "
                "or shut down by the drive, so it reads as stuck or lagging -- "
                "but that is the symptom, not the cause.".format(what, name),
                "Let it cool, then look for what made it work so hard: a "
                "dragging shaft, a stroke or speed beyond what this piston can "
                "sustain, or a seal starting to seize.",
                "certain",
            ))
            break
    if _bit(warn, 1):
        found.append(Finding(
            "Thermal", "The drive calculates the motor is overheating",
            "Overload is a model of heat from current over time rather than a "
            "measured temperature, so it trips before the thermal sensor does.",
            "Reduce speed or acceleration for piston {0}, or give it a rest "
            "between runs.".format(name),
            "certain",
        ))
    if _bit(warn, 10):
        found.append(Finding(
            "Thermal", "The brake resistor is calculated to be hot",
            "Repeated hard decelerations put energy into the regeneration "
            "resistor faster than it sheds it.",
            "Lower the deceleration, or run fewer strokes back to back.",
            "certain",
        ))
    if _bit(warn, 6):
        found.append(Finding(
            "Thermal", "The servo controller is hot",
            "Controller Hot (warn bit 6) is set. This is the controller's "
            "thermal warning; it does not establish a mechanical jam.",
            "Check controller cooling, ventilation and loading before retrying.",
            "certain",
        ))
    for bit in (14, 15):
        if _bit(warn, bit):
            found.append(Finding(
                "Drive", drive_status.WARN_BITS[bit].name,
                drive_status.WARN_BITS[bit].meaning + ". The warn word alone "
                "does not identify the underlying cause.",
                "Inspect the drive's detailed diagnostics before retrying.",
                "certain",
            ))

    # -- electrical ---------------------------------------------------------
    if _bit(warn, 2) or _bit(warn, 3):
        which = "fallen" if _bit(warn, 2) else "risen"
        found.append(Finding(
            "Electrical", "Supply voltage is at its warn limit",
            "The drive's supply has {0} to the limit it warns at. A piston "
            "cannot make its commanded move on a supply like that, and it will "
            "look mechanically stuck.".format(which),
            "Check the supply and the wiring to this drive before touching "
            "anything mechanical.",
            "certain",
        ))
    if _bit(warn, 12):
        found.append(Finding(
            "Drive", "The position sensor is in a warning condition",
            "The encoder this piston homes and runs against is unhappy, so "
            "every position reading is suspect -- including whether it has "
            "homed at all.",
            "Check the sensor cable and connector on piston {0}. A position "
            "sensor that cannot be trusted cannot home.".format(name),
            "certain",
        ))

    # -- the drive's own error state ---------------------------------------
    if _bit(status, 12):
        found.append(Finding(
            "Drive", "The drive is in a fatal error state",
            "Status bit 12. The manual notes this one cannot be acknowledged, "
            "so Clear Faults will not shift it.",
            "This drive needs power-cycling, and if it comes back, attention "
            "from somebody with the LinMot manual.",
            "certain",
        ))
    elif _bit(status, 3):
        found.append(Finding(
            "Drive", "The drive is in an error state",
            "Status bit 3 is set. Homing and running are both refused while a "
            "drive is in error.",
            "Press Clear Faults, then home again. If it returns immediately, "
            "the cause is whatever else is listed here.",
            "certain",
        ))
    if _bit(status, 6):
        found.append(Finding(
            "Drive", "Switch-on is locked",
            "The drive is refusing to enable its power stage.",
            "Usually follows an error that has not been cleared. Clear faults "
            "and boot the motors.",
            "certain",
        ))

    # -- software and sequencing -------------------------------------------
    if facts.get("expected_live") and facts.get("live") is False:
        found.append(Finding(
            "Software", "This piston was never included in the run",
            "Its Live_Motors bit is clear on the PLC, so the ladder skipped it "
            "and every command went to the other pistons. Nothing is wrong "
            "with piston {0} -- it was never asked to move.".format(name),
            "Check it is selected on the tank and Prepare again. If it is "
            "selected here but clear on the PLC, the selection is not reaching "
            "the machine.",
            "certain",
        ))

    stroke = _stroke_held(facts)
    if (_parameters_relevant(facts) and _stroke_expected(facts)
            and stroke is not None and stroke < 1):
        found.append(Finding(
            "Software", "The drive has been given no stroke to run",
            "Position 1 and Position 2 on this drive are the same, so a "
            "perfectly healthy piston will sit still and report that it "
            "arrived. This is what the machine holds, not what was typed -- "
            "the two differ when a parameter write fails.",
            "Send the parameters again, then check Position 1 and Position 2 "
            "for piston {0} on the Operate tab.".format(name),
            "certain",
        ))

    wanted = facts.get("wanted_params") or {}
    held = facts.get("held_params") or {}
    drifted = []
    for key in ("Position 1", "Position 2", "Speed 1", "Speed 2"):
        if key not in wanted or held.get(key) is None:
            continue
        try:
            if float(held[key]) != float(wanted[key]):
                drifted.append(key)
        except (TypeError, ValueError):
            continue
    if drifted and _parameters_relevant(facts):
        found.append(Finding(
            "Software", "The drive is not holding the requested parameters",
            "{0} differ between the application's desired settings and the "
            "values read from the PLC. The desired settings may not have been "
            "sent yet. These readings are sequential, so a concurrent parameter "
            "write can also produce a temporary mismatch; this does not prove "
            "a failed write.".format(", ".join(drifted)),
            "When preparation has finished, read the parameters again. If the "
            "mismatch remains, check the intended settings and prepare again "
            "when appropriate.",
            "certain",
        ))

    # -- mechanical last: only once the above are ruled out -----------------
    homed = _bit(status, 11)
    enabled = _bit(status, 0)
    moved = facts.get("probe_movement_mm")
    lagging = drive_status.is_lagging(warn)
    error_mm = None
    invalid_positions = [key for key in ("actual_mm", "demand_mm")
                         if facts.get(key) is not None
                         and not _valid_position(facts[key])]
    if invalid_positions:
        found.append(Finding(
            "Drive", "Position feedback is unreliable",
            "{0} contains a non-finite value or a value outside the configured "
            "drive envelope (-57 to 453 mm). Following error cannot be "
            "interpreted reliably. A faulted drive, stale data, a conversion "
            "problem or sensor trouble are possibilities, not proven causes.".format(
                ", ".join(invalid_positions)),
            "Check drive fault details and position units, then compare fresh "
            "readings with an independent observation before commanding motion.",
            "certain",
        ))
    if _valid_position(facts.get("actual_mm")) and _valid_position(facts.get("demand_mm")):
        error_mm = abs(facts["demand_mm"] - facts["actual_mm"])

    if not enabled and not _already_explained(found):
        found.append(Finding(
            "Software", "The power stage is not enabled",
            "Status bit 0 is clear, so this drive is not energised. It cannot "
            "home and cannot move, and no fault is set to explain it -- which "
            "points at the boot sequence rather than at the hardware.",
            "Press Prepare, which boots the motors, and watch whether this "
            "piston enables with the rest.",
            "certain",
        ))

    # After the power stage, not before it: a probe that raised on a drive
    # that was never energised is explained by the drive being de-energised,
    # and "the movement test did not complete" on its own sends nobody
    # anywhere useful.
    if facts.get("probe_error"):
        found.append(Finding(
            "Unknown", "The movement test did not complete",
            "{0}. No valid movement result is available, so this test cannot "
            "establish whether the piston moved or is mechanically stuck.".format(
                facts["probe_error"]),
            "Check the reported error and the machine's current state before "
            "deciding whether another movement test is appropriate.",
            "certain",
        ))

    if (enabled and not _already_explained(found)
            and moved is not None and moved < NO_MOVEMENT_MM):
        found.append(Finding(
            "Mechanical", "Commanded, energised, and it did not move",
            "The drive is enabled and took the command, and the encoder "
            "reports it travelled {0:.1f} mm. Mechanical resistance is one "
            "possibility, but unchanged feedback does not prove a jam or "
            "frozen data; command delivery and feedback need checking.".format(moved),
            "Check piston {0} for a seized shaft, a jammed seal, debris in the "
            "track, or a mechanical stop in the way. Also verify command "
            "delivery and position feedback.".format(name),
            "likely",
        ))
    elif (enabled and not _already_explained(found) and lagging
            and error_mm is not None and error_mm > STUCK_ERROR_MM):
        found.append(Finding(
            "Mechanical", "Falling a long way behind its commanded position",
            "The drive reports following error and the gap is {0:.1f} mm. It "
            "is pushing and not getting there, which is drag rather than a "
            "hard jam.".format(error_mm),
            "Check piston {0} for stiffness through its travel. If it is stiff "
            "in one part of the stroke, suspect the seal or the alignment "
            "there.".format(name),
        ))
    elif enabled and not _already_explained(found) and lagging:
        found.append(Finding(
            "Mechanical", "The drive reports it cannot keep up",
            "Position or speed lag is set, from the drive's own configured "
            "limit, and nothing else here explains it.",
            "Try piston {0} at a lower speed. If it keeps up slowly but not "
            "quickly, it is dragging rather than stuck.".format(name),
        ))

    if not homed and not found:
        found.append(Finding(
            "Unknown", "Not homed, and nothing says why",
            "The available readings do not explain why the drive has not "
            "referenced its position sensor. The warn word may include the "
            "routine Not Homed flag; missing readings cannot exclude a fault.",
            "Observe an authorised homing operation on piston {0}. If it "
            "does not move, check command delivery, drive readiness and "
            "mechanical resistance; if it moves without finishing, also "
            "check the position sensor and its reference.".format(name),
        ))
    return found


def diagnose(motor, plc, expected_live: Optional[bool] = None,
             probe: Optional[Callable[[], float]] = None) -> Report:
    """Read everything about one piston and say what is wrong with it.

    *probe*, if given, is called to command a small movement and return how far
    the piston actually travelled. It is what separates "cannot move" from
    "was never told to", so the tool offers it as a deliberate second step
    rather than doing it unasked.
    """
    facts = gather(motor, plc, expected_live=expected_live)
    if probe is not None:
        try:
            movement = float(probe())
            if not math.isfinite(movement) or movement < 0:
                raise ValueError("Movement test returned an invalid distance")
            facts["probe_movement_mm"] = movement
        except Exception as exc:  # noqa: BLE001 - expose failed tests in the report
            facts["probe_movement_mm"] = None
            facts["probe_error"] = str(exc) or type(exc).__name__

    findings = findings_for(facts)
    checks = checks_for(facts)
    axis = facts["axis"]
    if findings:
        summary = "Piston {0}: {1}".format(
            tags.display_number(axis), findings[0].headline)
    else:
        summary = "Piston {0}: nothing wrong that the machine will admit to.".format(
            tags.display_number(axis))
    return Report(axis=axis, checks=checks, findings=findings, summary=summary)
