"""Driving the wavemaker from a terminal.

The application is a window, and everything written to drive the array without
one has been a one-off script under ``tools/``: each rebuilt the model, each
re-implemented the checks before motion and the cleanup after it, and each
homed the pistons again because homing is remembered only by the process that
did it. This package is the one place that work is done.

It is split in two because of that last point. ``Model`` trusts a piston's
homed bit only if *this process* homed it, and booting the drives to home them
drops every piston to zero, which makes a wave. A command that started a new
process each time would therefore either home on every call or bypass the
check. So one long-lived **session** process holds the PLC connection and the
model (``daemon``, ``service``), and the commands typed at the terminal are
thin clients that ask it to do things (``cli``, ``client``).

Nothing the application imports depends on this package, and apart from the
optional camera measuring it needs only the standard library.
"""

VERSION = "1"
