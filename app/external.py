"""Launching the other programs the lab uses.

Kept here so the launcher script does not have to know about them and the
application can offer them on demand instead of making every start-up walk
through them.
"""

from __future__ import annotations

import os
from logging import getLogger
from typing import Optional

from app import paths

LOGGER = getLogger("logger")

#: The Studio 5000 project, relative to the folder this application lives in.
STUDIO_PROJECT = (
    paths.APP_DIR.parent / "WaveMaker Programs" / "Studio 5000" / "Wavemaker_for_Python.ACD"
)


def studio_project_path() -> Optional[str]:
    """The PLC project file, or ``None`` if it is not where it should be."""
    return str(STUDIO_PROJECT) if STUDIO_PROJECT.exists() else None


def open_studio_5000() -> bool:
    """Open the PLC project in Studio 5000.

    Offered as a button rather than run at every launch: the application does
    not need Studio 5000 to talk to the controller, so it is only wanted when
    somebody needs to go online -- to put the controller back in Run, or to
    look at the ladder.
    """
    target = studio_project_path()
    if target is None:
        LOGGER.error("Studio 5000 project not found at %s", STUDIO_PROJECT)
        return False
    try:
        os.startfile(target)  # noqa: S606 - a known project file in this install
    except OSError as exc:
        LOGGER.error("Could not open the Studio 5000 project: %s", exc)
        return False
    LOGGER.info("Opened the Studio 5000 project.")
    return True


def open_folder(path) -> bool:
    try:
        os.startfile(str(path))  # noqa: S606 - this application's own folders
    except OSError as exc:
        LOGGER.error("Could not open %s: %s", path, exc)
        return False
    return True
