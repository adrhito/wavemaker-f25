"""Filesystem locations used by the application.

Every path is resolved relative to the installation directory rather than the
process working directory.  The old code used ``os.getcwd()``, which meant the
app only found its ``logs/``, ``analytics/`` and ``Presets/`` folders when it
happened to be started from its own directory -- one of the reasons the
launcher had to ``cd`` to a hard-coded path first.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

#: Directory containing ``main.py``.
APP_DIR: Path = Path(__file__).resolve().parent.parent

LOG_DIR: Path = APP_DIR / "logs"
ANALYTICS_DIR: Path = APP_DIR / "analytics"
PRESET_DIR: Path = APP_DIR / "Presets"


def ensure_directories() -> None:
    """Create the writable directories the app needs, if they are missing."""
    for directory in (LOG_DIR, ANALYTICS_DIR, PRESET_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def log_file(day: date | None = None) -> Path:
    """Path of the log file for ``day`` (today by default)."""
    return LOG_DIR / f"{day or date.today()}.log"


def analytics_file(day: date | None = None) -> Path:
    """Path of the analytics file for ``day`` (today by default)."""
    return ANALYTICS_DIR / f"{day or date.today()}.txt"
