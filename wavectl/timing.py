"""How long things are waited for.

Kept apart from the operations so the command line can size its own wait for
an answer without importing the model, which costs a noticeable start-up time
on every command.
"""

#: Longest a prepare is waited for: fault clear, boot, two homing passes and
#: the parameter writes, with room for thirty pistons.
PREPARE_SECONDS = 240.0
#: Longest a run may take to get going once it is prepared: staging a
#: travelling wave and the pulse that sets every drive on the same leg.
START_SECONDS = 60.0
#: Longest one stroke or one curve is waited for. Each leg may be held for up
#: to ``Model.MAX_STROKE_SECONDS``, and a stroke is three moves.
ONCE_SECONDS = 420.0
#: Longest the pistons are given to come to rest after a stop: finishing the
#: stroke, then the gentle move to the resting position.
REST_SECONDS = 45.0
#: A continuous run may not be asked to last longer than this.
MAX_RUN_SECONDS = 3600.0
#: How long a continuous run lasts when no length is given.
DEFAULT_RUN_SECONDS = 30.0
#: Added to what the session was asked to wait, so its answer -- including
#: "still under way" -- arrives before the command gives up on the connection.
ANSWER_MARGIN_SECONDS = 20.0
