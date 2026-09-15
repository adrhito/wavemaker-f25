"""The window has to satisfy the bridge the model calls back through.

These two faults were both invisible until the machine actually got far enough
into a run to use them, which on the real array is a minute of homing away:

* ``View`` built its progress bar as ``self.progress``, over the top of its own
  ``progress`` bridge method. Every ``bridge.progress(...)`` from the model --
  the analytics write at the end of a run -- therefore tried to *call* a
  ``ttk.Progressbar``.
* ``View._tab_changed`` routed by position through a tuple that had Diagnostics
  missing from it, so selecting Diagnostics ran Feedback's ``onSelect`` and
  selecting Feedback ran nothing.

A Tk root is needed to catch either, because both are about the instance rather
than the class. Skipped rather than failed where no display exists.
"""

from __future__ import annotations

import pytest

from Model import MachineState, Model, UiBridge


@pytest.fixture(scope="module")
def window():
    """One real window, withdrawn, shared by every test in this file.

    ``View`` creates its own ``tk.Tk()``, so this must not create one as well,
    and it is built once rather than per test. Standing a Tk root up and tearing
    it down repeatedly in one process fails part way through on Windows with
    "tk wasn't installed properly" -- which it is. Per-test roots also meant a
    TclError was caught and turned into a skip, so a genuine failure would have
    been reported as "no display".
    """
    tk = pytest.importorskip("tkinter")

    from View import View

    model = Model(simulate=True)
    try:
        view = View(model)
    except tk.TclError as exc:  # pragma: no cover - headless machine
        pytest.skip("no display available: {0}".format(exc))
    view.root.withdraw()
    try:
        yield view
    finally:
        try:
            view.model.shutdown()
        except Exception:  # pragma: no cover - best effort
            pass
        try:
            view.root.destroy()
        except Exception:  # pragma: no cover - already gone
            pass


def pump_until(view, predicate, seconds=3.0):
    """Drive the Tk loop until ``predicate`` holds.

    Bridge calls are queued and drained by ``View._pump`` on a 40 ms timer, so
    a single ``update()`` races it. Waiting on the result rather than on a
    fixed sleep keeps this deterministic.
    """
    import time

    deadline = time.time() + seconds
    while time.time() < deadline:
        view.root.update()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _bridge_methods():
    return [name for name in vars(UiBridge)
            if not name.startswith("_") and callable(getattr(UiBridge, name))]


def test_every_bridge_method_is_callable_on_the_window(window):
    """No widget may be assigned over the top of a bridge callback."""
    for name in _bridge_methods():
        assert callable(getattr(window, name)), (
            "View.{0} is not callable -- a widget has shadowed the bridge "
            "method of the same name".format(name)
        )


def test_progress_reaches_the_progress_bar(window):
    """The call the model makes while writing analytics must not raise."""
    window.progress(0.4, "Recording analytics")
    assert pump_until(
        window, lambda: window.progress_bar["value"] == pytest.approx(40.0)
    ), "progress never reached the bar"

    window.hide_progress()
    assert window.progress_bar["value"] == 0


def test_state_changed_reaches_every_tab(window):
    """A state change must survive the trip through the callback queue."""
    window.state_changed(MachineState.READY)
    assert pump_until(
        window, lambda: window.state_chip.cget("text") == "READY"
    ), "state change never reached the status bar"


def test_tab_routing_matches_the_notebook_order(window):
    """Selecting a tab must run that tab's own onSelect, not its neighbour's."""
    order = (window.operate, window.wave, window.preset_options,
             window.diagnostics, window.feedback)
    assert window.tabControl.index("end") == len(order)

    for position, tab in enumerate(order):
        window.tabControl.select(position)
        window.root.update()
        assert window.tabControl.index(window.tabControl.select()) == position
        # The tab at this position is the one the router would act on.
        assert tab is order[position]
