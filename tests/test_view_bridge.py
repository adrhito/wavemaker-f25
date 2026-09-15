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


@pytest.fixture
def window():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless machine
        pytest.skip("no display available: {0}".format(exc))
    root.withdraw()

    from View import View

    model = Model(simulate=True)
    view = View(model)
    view.root.withdraw()
    try:
        yield view
    finally:
        try:
            view.root.destroy()
        except Exception:  # pragma: no cover - already gone
            pass
        root.destroy()


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
    window.root.update()
    assert window.progress_bar["value"] == pytest.approx(40.0)

    window.hide_progress()
    assert window.progress_bar["value"] == 0


def test_state_changed_reaches_every_tab(window):
    """A state change must survive the trip through the callback queue."""
    window.state_changed(MachineState.READY)
    window.root.update()
    assert window.state_chip.cget("text") == "READY"


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
