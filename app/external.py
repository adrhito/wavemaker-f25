"""Opening things outside the application.

This used to launch the Studio 5000 project as well, as a way back into Run
when the controller had dropped out of it. That could only work when the
processor's keyswitch was in REM, which is the one case an operator can also
fix by turning the key -- and at RUN or PROG the key is the only thing that
decides. The button was removed and replaced with advice; see
``Operate.explain_connection``.
"""

from __future__ import annotations

import os
from logging import getLogger

LOGGER = getLogger("logger")


def open_folder(path) -> bool:
    try:
        os.startfile(str(path))  # noqa: S606 - this application's own folders
    except OSError as exc:
        LOGGER.error("Could not open %s: %s", path, exc)
        return False
    return True
