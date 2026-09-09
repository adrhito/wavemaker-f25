"""Reading and writing preset CSV files.

The reader is deliberately forgiving, because the files in ``Presets/`` are
edited by hand in Excel and most of them do not parse under the old rules.  Of
the fourteen presets shipped with the application, several contain blank
separator rows, and some list only a couple of pistons instead of all thirty.
The old reader called ``int('')`` on a blank cell and indexed row thirty of a
two-row table, and the one caller wrapped the whole thing in a bare ``except``
that logged at debug level -- so a preset that could not be read looked exactly
like one that had been applied.

Blank rows, missing pistons, extra columns and stray whitespace are now all
handled, and anything genuinely wrong is raised as :class:`PresetError` with a
message written for the operator.
"""

from __future__ import annotations

import csv
import os
from logging import Logger, getLogger
from typing import Dict, Iterable, List, Optional

from app import params, paths
from modules.logging.log_utils import LOGGER_NAME
from preset_options.Preset import Preset

LOGGER: Logger = getLogger(LOGGER_NAME)

MOTOR_COLUMN = "Motor"
ALL_ROW = "All"


class PresetError(Exception):
    """A preset file could not be read or written. The message is shown to the
    operator, so it says what is wrong and where."""


class PresetProcessor:
    """Loads and saves presets."""

    def __init__(self, model=None) -> None:
        self.model = model
        #: Column order used when writing.
        self.columns: List[str] = [MOTOR_COLUMN] + params.PARAM_NAMES

    # -- reading --------------------------------------------------------------

    def load(self, filename: str) -> Preset:
        """Read a preset file. Raises :class:`PresetError` if it cannot be used."""
        try:
            with open(filename, "r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    raise PresetError(
                        "{0} is empty.".format(os.path.basename(filename))
                    )
                headers = [
                    (name or "").strip() for name in reader.fieldnames
                ]
                self._check_headers(headers, filename)
                raw_rows = list(reader)
        except PresetError:
            raise
        except UnicodeDecodeError as exc:
            raise PresetError(
                "{0} is not a text CSV file ({1}).".format(
                    os.path.basename(filename), exc
                )
            ) from exc
        except OSError as exc:
            raise PresetError(
                "Could not open {0}: {1}".format(os.path.basename(filename), exc)
            ) from exc

        rows: Dict[int, Dict[str, int]] = {}
        all_row: Optional[Dict[str, int]] = None
        warnings: List[str] = []

        for line_number, raw in enumerate(raw_rows, start=2):
            cleaned = dict(
                ((key or "").strip(), (value or "").strip())
                for key, value in raw.items()
                if key is not None
            )
            label = cleaned.get(MOTOR_COLUMN, "")

            # Skip separator rows, which several shipped presets contain.
            if not label and not any(cleaned.get(p) for p in params.PARAM_NAMES):
                continue

            values, row_warnings = self._parse_row(cleaned, line_number)
            warnings.extend(row_warnings)

            if label.lower() == ALL_ROW.lower():
                all_row = values
                continue

            try:
                axis = int(label)
            except ValueError:
                warnings.append(
                    "Line {0}: ignored, '{1}' is not a motor number or 'All'.".format(
                        line_number, label
                    )
                )
                continue

            if not 0 <= axis < 30:
                warnings.append(
                    "Line {0}: ignored, motor {1} is outside 0-29.".format(
                        line_number, axis
                    )
                )
                continue

            rows[axis] = values

        if not rows and all_row is None:
            raise PresetError(
                "{0} contains no motor rows.".format(os.path.basename(filename))
            )

        preset = Preset(
            rows=rows,
            all_row=all_row,
            name=os.path.splitext(os.path.basename(filename))[0],
            warnings=warnings,
        )
        for warning in warnings:
            LOGGER.warning("%s: %s", preset.name, warning)
        LOGGER.info("Loaded preset %s", preset.describe())
        return preset

    def _check_headers(self, headers: List[str], filename: str) -> None:
        if MOTOR_COLUMN not in headers:
            raise PresetError(
                "{0} has no '{1}' column. The first column must be '{1}'.".format(
                    os.path.basename(filename), MOTOR_COLUMN
                )
            )
        missing = [name for name in params.PARAM_NAMES if name not in headers]
        if missing:
            raise PresetError(
                "{0} is missing {1} column(s): {2}".format(
                    os.path.basename(filename), len(missing), ", ".join(missing)
                )
            )

    def _parse_row(self, cleaned: Dict[str, str], line_number: int):
        """Turn one CSV row into parameter values, defaulting anything blank."""
        values: Dict[str, int] = {}
        warnings: List[str] = []
        for spec in params.PARAMS:
            text = cleaned.get(spec.name, "")
            if text == "":
                values[spec.name] = spec.default
                continue
            try:
                values[spec.name] = int(float(text))
            except ValueError:
                values[spec.name] = spec.default
                warnings.append(
                    "Line {0}: {1} is '{2}', which is not a number; used {3}.".format(
                        line_number, spec.name, text, spec.default
                    )
                )
        return values, warnings

    # -- writing --------------------------------------------------------------

    def save(self, filename: str, motor_sets: Iterable) -> str:
        """Write the parameters of ``motor_sets`` to a preset file.

        Saves every confirmed set, not just the pistons that happened to be
        pending -- the old writer read a dictionary that was empty by the time
        a set had been confirmed, so saving after defining sets produced a file
        of zeroes.
        """
        target = self._resolve_path(filename)

        by_axis: Dict[int, Dict[str, int]] = {}
        for motor_set in motor_sets:
            for motor in motor_set:
                by_axis[motor.axis] = dict(motor.write_params)

        if not by_axis:
            raise PresetError(
                "There are no motor sets to save. Create a set on the Define "
                "Motors tab first."
            )

        placeholder = params.defaults()
        placeholder.update(dict((spec.name, 0) for spec in params.PARAMS))
        last_real: Optional[Dict[str, int]] = None

        try:
            with open(target, "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(self.columns)
                for axis in range(30):
                    row = by_axis.get(axis)
                    if row is None:
                        writer.writerow([axis] + [placeholder[n] for n in params.PARAM_NAMES])
                    else:
                        last_real = row
                        writer.writerow([axis] + [row[n] for n in params.PARAM_NAMES])
                fallback = last_real or placeholder
                writer.writerow([ALL_ROW] + [fallback[n] for n in params.PARAM_NAMES])
        except OSError as exc:
            raise PresetError("Could not write {0}: {1}".format(target, exc)) from exc

        LOGGER.log(15, "Saved preset to %s", target)
        return target

    def _resolve_path(self, filename: str) -> str:
        name = (filename or "").strip()
        if not name:
            raise PresetError("Give the preset a name.")

        invalid = set('<>:"/\\|?*')
        if os.path.dirname(name) == "" and not invalid.isdisjoint(name):
            raise PresetError(
                "A preset name cannot contain any of  < > : \" / \\ | ? *"
            )

        if not name.lower().endswith(".csv"):
            name += ".csv"
        if os.path.isabs(name) or os.path.dirname(name):
            return name
        paths.ensure_directories()
        return str(paths.PRESET_DIR / name)

    # -- backwards-compatible names -------------------------------------------

    def processPreset(self, filename: str) -> Preset:
        """Old name for :meth:`load`, kept so external scripts keep working."""
        return self.load(filename)

    def create_preset(self, filename: str) -> str:
        """Old name for :meth:`save`, using the model's sets."""
        return self.save(filename, self.model.sets if self.model else [])
