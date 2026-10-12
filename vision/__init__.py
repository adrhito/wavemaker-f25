"""Measuring the water with a camera.

Everything else in this repository runs on the lab's Windows 7 computer with
nothing but the standard library.  This package does not: it needs NumPy and
OpenCV (``requirements-vision.txt``) and runs on whichever laptop the camera is
plugged into.  Nothing under ``app/``, ``Model.py`` or the tabs imports it, so
the application still starts on a machine that has neither.

The camera looks through the tank's clear side wall at the water in profile.
``calibration`` finds the printed markers taped to that wall and turns pixels
into millimetres, ``waterline`` reads the height of the surface along the
view, and ``metrics`` turns a run of those readings into the numbers a trial
is recorded with.  ``synthetic`` draws a tank with a known wave on it, which
is the only thing these modules have been checked against so far.
"""
